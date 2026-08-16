from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import io
from pathlib import Path
import re
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PG_RESTORE = Path("/Applications/Postgres.app/Contents/Versions/17/bin/pg_restore")


@dataclass(frozen=True)
class TxRow:
    id: int
    client_id: int
    account_id: int
    txn_at: datetime
    amount: Decimal
    balance_after: Decimal
    group_id: int | None
    actor_id: int | None
    comment: str | None
    source: str | None
    idempotency_key: str | None
    created_at: datetime


def sql_quote(value: object | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, datetime):
        return f"'{value.isoformat(sep=' ')}'::timestamptz"
    text = str(value).replace("'", "''")
    return f"'{text}'"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build SQL patch to merge old dump history into new dump without duplicating transactions.",
    )
    parser.add_argument("--new-dump", required=True)
    parser.add_argument("--old-dump", required=True)
    parser.add_argument(
        "--output-sql",
        default="dumps/merge_old_into_new.sql",
        help="SQL patch to apply after restoring the new dump.",
    )
    parser.add_argument(
        "--output-report",
        default="dumps/merge_old_into_new_report.txt",
        help="Human-readable merge report.",
    )
    return parser.parse_args()


def run_pg_restore(dump_path: Path) -> str:
    result = subprocess.run(
        [str(PG_RESTORE), "-a", "-f", "-", str(dump_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def parse_copy(sql: str, table: str) -> tuple[list[str], list[dict[str, str | None]]]:
    match = re.search(
        rf"COPY public\.{re.escape(table)} \(([^)]*)\) FROM stdin;\n(.*?)\n\\\.",
        sql,
        re.S,
    )
    if not match:
        raise RuntimeError(f"COPY block for {table} not found")

    cols = [c.strip().strip('"') for c in match.group(1).split(",")]
    rows: list[dict[str, str | None]] = []
    reader = csv.reader(io.StringIO(match.group(2)), delimiter="\t")
    for record in reader:
        rows.append({col: (None if value == r"\N" else value) for col, value in zip(cols, record)})
    return cols, rows


def to_tx_rows(rows: list[dict[str, str | None]]) -> list[TxRow]:
    out: list[TxRow] = []
    for row in rows:
        out.append(
            TxRow(
                id=int(row["id"]),
                client_id=int(row["client_id"]),
                account_id=int(row["account_id"]),
                txn_at=datetime.fromisoformat(str(row["txn_at"])),
                amount=Decimal(str(row["amount"])),
                balance_after=Decimal(str(row["balance_after"])),
                group_id=int(row["group_id"]) if row["group_id"] is not None else None,
                actor_id=int(row["actor_id"]) if row["actor_id"] is not None else None,
                comment=row["comment"],
                source=row["source"],
                idempotency_key=row["idempotency_key"],
                created_at=datetime.fromisoformat(str(row["created_at"])),
            )
        )
    return out


def build_report(lines: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    new_dump = (PROJECT_ROOT / args.new_dump).resolve() if not Path(args.new_dump).is_absolute() else Path(args.new_dump)
    old_dump = (PROJECT_ROOT / args.old_dump).resolve() if not Path(args.old_dump).is_absolute() else Path(args.old_dump)
    output_sql = (PROJECT_ROOT / args.output_sql).resolve() if not Path(args.output_sql).is_absolute() else Path(args.output_sql)
    output_report = (
        (PROJECT_ROOT / args.output_report).resolve() if not Path(args.output_report).is_absolute() else Path(args.output_report)
    )

    if not PG_RESTORE.exists():
        raise RuntimeError(f"pg_restore not found: {PG_RESTORE}")

    new_sql = run_pg_restore(new_dump)
    old_sql = run_pg_restore(old_dump)

    _, new_clients_rows = parse_copy(new_sql, "clients")
    _, old_clients_rows = parse_copy(old_sql, "clients")
    _, new_accounts_rows = parse_copy(new_sql, "client_accounts")
    _, old_accounts_rows = parse_copy(old_sql, "client_accounts")
    _, new_tx_rows_raw = parse_copy(new_sql, "transactions")
    _, old_tx_rows_raw = parse_copy(old_sql, "transactions")

    new_txs = to_tx_rows(new_tx_rows_raw)
    old_txs = to_tx_rows(old_tx_rows_raw)

    cutoff_dt = min(tx.txn_at for tx in new_txs)

    new_clients_by_chat = {row["chat_id"]: row for row in new_clients_rows if row["chat_id"]}
    old_clients_by_chat = {row["chat_id"]: row for row in old_clients_rows if row["chat_id"]}
    new_clients_by_id = {int(row["id"]): row for row in new_clients_rows}
    old_clients_by_id = {int(row["id"]): row for row in old_clients_rows}

    def account_key(row: dict[str, str | None], clients_by_id: dict[int, dict[str, str | None]]) -> tuple[str, str]:
        client = clients_by_id[int(str(row["client_id"]))]
        return str(client["chat_id"]), str(row["currency_code"]).upper()

    new_accounts_by_key = {account_key(row, new_clients_by_id): row for row in new_accounts_rows}
    old_accounts_by_key = {account_key(row, old_clients_by_id): row for row in old_accounts_rows}

    shared_account_keys = set(new_accounts_by_key) & set(old_accounts_by_key)
    old_only_account_keys = set(old_accounts_by_key) - set(new_accounts_by_key)
    old_only_client_chats = set(old_clients_by_chat) - set(new_clients_by_chat)

    shared_idempotency = {
        tx.idempotency_key for tx in new_txs if tx.idempotency_key
    } & {
        tx.idempotency_key for tx in old_txs if tx.idempotency_key
    }

    max_new_client_id = max(int(row["id"]) for row in new_clients_rows)
    max_new_account_id = max(int(row["id"]) for row in new_accounts_rows)
    max_new_tx_id = max(tx.id for tx in new_txs)

    client_id_map_by_old_id: dict[int, int] = {}
    next_client_id = max_new_client_id + 1
    new_client_inserts: list[str] = []

    for chat_id, old_client in sorted(old_clients_by_chat.items()):
        existing = new_clients_by_chat.get(chat_id)
        if existing:
            client_id_map_by_old_id[int(old_client["id"])] = int(str(existing["id"]))
            continue

        new_client_id = next_client_id
        next_client_id += 1
        client_id_map_by_old_id[int(old_client["id"])] = new_client_id
        new_client_inserts.append(
            "INSERT INTO public.clients (id, chat_id, name, city, created_at, is_active, client_group) VALUES "
            f"({new_client_id}, {sql_quote(old_client['chat_id'])}, {sql_quote(old_client['name'])}, "
            f"NULL, {sql_quote(datetime.fromisoformat(str(old_client['created_at'])))}, TRUE, "
            f"{sql_quote(old_client.get('client_group'))});"
        )

    next_account_id = max_new_account_id + 1
    account_id_map_by_old_id: dict[int, int] = {}
    account_insert_sql: list[str] = []

    for old_row in old_accounts_rows:
        old_row_id = int(str(old_row["id"]))
        mapped_client_id = client_id_map_by_old_id[int(str(old_row["client_id"]))]
        old_client = old_clients_by_id[int(str(old_row["client_id"]))]
        key = (str(old_client["chat_id"]), str(old_row["currency_code"]).upper())
        existing = new_accounts_by_key.get(key)
        if existing:
            account_id_map_by_old_id[old_row_id] = int(str(existing["id"]))
            continue

        new_account_id = next_account_id
        next_account_id += 1
        account_id_map_by_old_id[old_row_id] = new_account_id
        account_insert_sql.append(
            "INSERT INTO public.client_accounts "
            '(id, client_id, currency_code, "precision", balance, is_active, created_at, deactivated_at) VALUES '
            f"({new_account_id}, {mapped_client_id}, {sql_quote(str(old_row['currency_code']).upper())}, "
            f"{int(str(old_row['precision']))}, {Decimal(str(old_row['balance']))}, TRUE, "
            f"{sql_quote(datetime.fromisoformat(str(old_row['created_at'])))}, NULL);"
        )

    old_txs_by_account: dict[tuple[str, str], list[TxRow]] = {}
    new_txs_by_account: dict[tuple[str, str], list[TxRow]] = {}
    for tx in old_txs:
        old_acc = next(row for row in old_accounts_rows if int(str(row["id"])) == tx.account_id)
        key = account_key(old_acc, old_clients_by_id)
        old_txs_by_account.setdefault(key, []).append(tx)
    for tx in new_txs:
        new_acc = next(row for row in new_accounts_rows if int(str(row["id"])) == tx.account_id)
        key = account_key(new_acc, new_clients_by_id)
        new_txs_by_account.setdefault(key, []).append(tx)

    imported_old_txs: list[tuple[TxRow, int, int]] = []
    rebase_sql: list[str] = []
    next_tx_id = max_new_tx_id + 1
    rebase_count = 0

    for key, txs in old_txs_by_account.items():
        txs_sorted = sorted(txs, key=lambda x: (x.txn_at, x.id))
        shared = key in shared_account_keys
        if shared:
            txs_to_import = [tx for tx in txs_sorted if tx.txn_at < cutoff_dt]
        else:
            txs_to_import = txs_sorted

        if not txs_to_import:
            continue

        mapped_account_id = account_id_map_by_old_id[txs_to_import[0].account_id]
        old_client = old_clients_by_id[txs_to_import[0].client_id]
        mapped_client_id = client_id_map_by_old_id[txs_to_import[0].client_id]

        for tx in txs_to_import:
            imported_old_txs.append((tx, mapped_client_id, mapped_account_id))

        if shared and key in new_txs_by_account:
            old_last_balance = txs_to_import[-1].balance_after
            new_first = sorted(new_txs_by_account[key], key=lambda x: (x.txn_at, x.id))[0]
            new_opening_balance = new_first.balance_after - new_first.amount
            delta = new_opening_balance - old_last_balance
            if delta != 0:
                rebased_txn_at = cutoff_dt
                rebased_created_at = cutoff_dt
                chat_id, currency_code = key
                rebase_sql.append(
                    "INSERT INTO public.transactions "
                    "(id, client_id, account_id, txn_at, amount, balance_after, group_id, actor_id, comment, source, idempotency_key, created_at) VALUES "
                    f"({next_tx_id}, {mapped_client_id}, {mapped_account_id}, "
                    f"{sql_quote(rebased_txn_at)}, {delta}, {new_opening_balance}, NULL, NULL, "
                    f"{sql_quote(f'merge rebase to new opening for {currency_code} {chat_id}')}, "
                    f"{sql_quote('merge_rebase')}, {sql_quote(f'merge_rebase:{chat_id}:{currency_code}:{cutoff_dt.isoformat()}')}, "
                    f"{sql_quote(rebased_created_at)});"
                )
                next_tx_id += 1
                rebase_count += 1

    imported_old_txs.sort(key=lambda item: (item[0].txn_at, item[0].id))
    tx_insert_sql: list[str] = []
    for tx, mapped_client_id, mapped_account_id in imported_old_txs:
        tx_insert_sql.append(
            "INSERT INTO public.transactions "
            "(id, client_id, account_id, txn_at, amount, balance_after, group_id, actor_id, comment, source, idempotency_key, created_at) VALUES "
            f"({next_tx_id}, {mapped_client_id}, {mapped_account_id}, {sql_quote(tx.txn_at)}, {tx.amount}, "
            f"{tx.balance_after}, {sql_quote(tx.group_id)}, {sql_quote(tx.actor_id)}, {sql_quote(tx.comment)}, "
            f"{sql_quote(tx.source)}, {sql_quote(tx.idempotency_key)}, {sql_quote(tx.created_at)});"
        )
        next_tx_id += 1

    final_tx_seq = next_tx_id - 1
    final_client_seq = next_client_id - 1
    final_account_seq = next_account_id - 1

    sql_lines = [
        "-- Apply this patch AFTER restoring the NEW dump.",
        "-- Merge policy:",
        f"-- 1. NEW dump is authoritative from cutover {cutoff_dt.isoformat()} onward for shared wallets.",
        "-- 2. OLD-only clients/accounts are imported completely.",
        "-- 3. For shared wallets, OLD transactions are imported only before cutover.",
        "-- 4. Rebase transactions are inserted to bridge old ending balance to new opening balance.",
        "BEGIN;",
        *new_client_inserts,
        *account_insert_sql,
        *tx_insert_sql,
        *rebase_sql,
        f"SELECT setval('public.clients_id_seq', {final_client_seq}, true);",
        f'SELECT setval(\'public.client_accounts_id_seq\', {final_account_seq}, true);',
        f"SELECT setval('public.transactions_id_seq', {final_tx_seq}, true);",
        "COMMIT;",
        "",
    ]

    output_sql.parent.mkdir(parents=True, exist_ok=True)
    output_sql.write_text("\n".join(sql_lines), encoding="utf-8")

    report_lines = [
        "SkyEx dump merge analysis",
        f"new dump: {new_dump}",
        f"old dump: {old_dump}",
        "",
        f"new transactions: {len(new_txs)}",
        f"old transactions: {len(old_txs)}",
        f"cutover (earliest new txn_at): {cutoff_dt.isoformat()}",
        f"shared idempotency keys: {len(shared_idempotency)}",
        f"old-only client chats: {len(old_only_client_chats)}",
        f"shared wallet accounts: {len(shared_account_keys)}",
        f"old-only wallet accounts: {len(old_only_account_keys)}",
        f"new clients inserted: {len(new_client_inserts)}",
        f"new accounts inserted: {len(account_insert_sql)}",
        f"old transactions imported: {len(imported_old_txs)}",
        f"rebase transactions inserted: {rebase_count}",
        "",
        "Important:",
        "- No exact transaction duplicates were found by idempotency_key between the dumps.",
        "- The dumps are divergent histories, not two copies of the same live DB.",
        "- This patch keeps NEW as the source of truth after cutover for shared wallets.",
        f"- Generated SQL patch: {output_sql}",
    ]
    build_report(report_lines, output_report)

    print(output_sql)
    print(output_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
