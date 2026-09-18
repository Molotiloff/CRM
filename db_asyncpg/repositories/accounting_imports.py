from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from domain import DomainStateError, DomainValidationError
from services.accounting.import_models import ImportedRecord, ImportRunState

from .base import ConnectionBoundRepo


class AccountingImportsRepo(ConnectionBoundRepo):
    async def acquire_source_lock(self, source_name: str) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('accounting_import:' || $1, 0))",
                source_name,
            )

    async def start_or_resume(
        self,
        *,
        source_name: str,
        manifest_checksum: str,
        record_count: int,
        strategy: dict[str, str],
        control_totals: dict[str, str],
    ) -> ImportRunState:
        async with self._connection() as connection:
            await connection.execute(
                """
                INSERT INTO accounting_import_runs(
                    source_name, manifest_checksum, status, record_count,
                    strategy, control_totals
                ) VALUES ($1, $2, 'running', $3, $4::jsonb, $5::jsonb)
                ON CONFLICT (source_name, manifest_checksum) DO NOTHING
                """,
                source_name,
                manifest_checksum,
                record_count,
                json.dumps(strategy),
                json.dumps(control_totals),
            )
            row = await connection.fetchrow(
                """
                SELECT id, status, checkpoint, record_count
                FROM accounting_import_runs
                WHERE source_name = $1 AND manifest_checksum = $2
                FOR UPDATE
                """,
                source_name,
                manifest_checksum,
            )
            if int(row["record_count"]) != record_count:
                raise DomainStateError("Import checksum belongs to another record set")
            if row["status"] == "failed":
                await connection.execute(
                    """
                    UPDATE accounting_import_runs
                    SET status = 'running', error_kind = NULL, updated_at = NOW()
                    WHERE id = $1
                    """,
                    int(row["id"]),
                )
                status = "running"
            else:
                status = str(row["status"])
        return ImportRunState(
            id=int(row["id"]),
            status=status,
            checkpoint=int(row["checkpoint"]),
            record_count=int(row["record_count"]),
        )

    async def imported_record(
        self, *, source_name: str, entity_kind: str, source_key: str
    ) -> ImportedRecord | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                SELECT payload_checksum, target_table, target_id
                FROM accounting_import_records
                WHERE source_name = $1 AND entity_kind = $2 AND source_key = $3
                """,
                source_name,
                entity_kind,
                source_key,
            )
        return (
            ImportedRecord(
                payload_checksum=str(row["payload_checksum"]),
                target_table=str(row["target_table"]),
                target_id=int(row["target_id"]),
            )
            if row is not None
            else None
        )

    async def record_applied(
        self,
        *,
        run_id: int,
        source_name: str,
        entity_kind: str,
        source_key: str,
        payload_checksum: str,
        sequence_no: int,
        target_table: str,
        target_id: int,
    ) -> None:
        async with self._connection() as connection:
            await connection.execute(
                """
                INSERT INTO accounting_import_records(
                    run_id, source_name, entity_kind, source_key, payload_checksum,
                    sequence_no, target_table, target_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                run_id,
                source_name,
                entity_kind,
                source_key,
                payload_checksum,
                sequence_no,
                target_table,
                target_id,
            )
            await connection.execute(
                """
                UPDATE accounting_import_runs
                SET checkpoint = GREATEST(checkpoint, $2), updated_at = NOW()
                WHERE id = $1
                """,
                run_id,
                sequence_no,
            )

    async def advance_checkpoint(self, run_id: int, *, sequence_no: int) -> None:
        async with self._connection() as connection:
            await connection.execute(
                """
                UPDATE accounting_import_runs
                SET checkpoint = GREATEST(checkpoint, $2), updated_at = NOW()
                WHERE id = $1
                """,
                run_id,
                sequence_no,
            )

    async def complete(self, run_id: int) -> None:
        async with self._connection() as connection:
            await connection.execute(
                """
                UPDATE accounting_import_runs
                SET status = 'completed', completed_at = NOW(), updated_at = NOW()
                WHERE id = $1 AND checkpoint = record_count
                """,
                run_id,
            )

    async def fail(self, run_id: int, *, error_kind: str) -> None:
        async with self._connection() as connection:
            await connection.execute(
                """
                UPDATE accounting_import_runs
                SET status = 'failed', error_kind = $2,
                    completed_at = NULL, updated_at = NOW()
                WHERE id = $1 AND status <> 'completed'
                """,
                run_id,
                error_kind,
            )

    async def resolve_client_id(self, chat_id: int) -> int:
        async with self._connection() as connection:
            client_id = await connection.fetchval(
                "SELECT id FROM clients WHERE chat_id = $1", chat_id
            )
        if client_id is None:
            raise DomainValidationError(f"Import client chat {chat_id} was not found")
        return int(client_id)

    async def deal(
        self,
        *,
        deal_type: str,
        city: str,
        deal_at: date,
        profit: Decimal,
        body: Mapping[str, object],
        comment: str | None,
        source_ref: str,
    ) -> int:
        async with self._connection() as connection:
            deal_id = await connection.fetchval(
                """
                INSERT INTO deals(
                    deal_type, city, status, source, source_kind, source_ref,
                    body, profit, deal_at, comment
                ) VALUES ($1, $2, 'done', 'import', 'accounting_import', $3,
                          $4::jsonb, $5, $6, $7)
                ON CONFLICT (source, source_kind, source_ref)
                    WHERE source_ref IS NOT NULL
                DO UPDATE SET source_ref = EXCLUDED.source_ref
                RETURNING id
                """,
                deal_type,
                city.strip().lower(),
                source_ref,
                json.dumps(dict(body), ensure_ascii=False),
                profit,
                deal_at,
                comment,
            )
        return int(deal_id)

    async def cash_registry(
        self, *, chat_id: int, city: str, location_name: str
    ) -> int:
        client_id = await self.resolve_client_id(chat_id)
        async with self._connection() as connection:
            row_id = await connection.fetchval(
                """
                INSERT INTO cash_chat_registry(chat_id, client_id, city, location_name)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (chat_id) DO UPDATE
                SET city = EXCLUDED.city, location_name = EXCLUDED.location_name
                RETURNING id
                """,
                chat_id,
                client_id,
                city.strip().lower(),
                location_name.strip(),
            )
        return int(row_id)

    async def internal_balance(
        self, *, name: str, kind: str, currency: str, amount: Decimal,
        idempotency_key: str
    ) -> int:
        async with self._connection() as connection:
            repeated = await connection.fetchval(
                "SELECT id FROM internal_account_moves WHERE idempotency_key = $1",
                idempotency_key,
            )
            if repeated is not None:
                return int(repeated)
            account = await connection.fetchrow(
                """
                INSERT INTO internal_accounts(name, kind, currency_code)
                VALUES ($1, $2, $3)
                ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id, balance, currency_code
                """,
                name.strip(),
                kind,
                currency,
            )
            if str(account["currency_code"]) != currency:
                raise DomainStateError("Internal account uses another currency")
            if Decimal(str(account["balance"])) != 0:
                raise DomainStateError("Internal account already has a nonzero balance")
            move_id = await connection.fetchval(
                """
                INSERT INTO internal_account_moves(
                    account_id, amount, balance_after, comment, idempotency_key
                ) VALUES ($1, $2, $2, 'Accounting import opening', $3)
                RETURNING id
                """,
                int(account["id"]),
                amount,
                idempotency_key,
            )
            await connection.execute(
                "UPDATE internal_accounts SET balance = $2 WHERE id = $1",
                int(account["id"]),
                amount,
            )
        return int(move_id)

    async def capital(
        self,
        *,
        owner: str,
        amount: Decimal,
        move_at: date,
        monthly_rate: Decimal | None,
        idempotency_key: str,
    ) -> int:
        async with self._connection() as connection:
            owner_id = await connection.fetchval(
                """
                INSERT INTO capital_owners(name, monthly_rate)
                VALUES ($1, $2)
                ON CONFLICT (name) DO UPDATE
                SET monthly_rate = COALESCE(capital_owners.monthly_rate, EXCLUDED.monthly_rate)
                RETURNING id
                """,
                owner.strip(),
                monthly_rate,
            )
            move_id = await connection.fetchval(
                """
                INSERT INTO capital_moves(owner_id, amount, move_at, comment, idempotency_key)
                VALUES ($1, $2, $3, 'Accounting import', $4)
                ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL
                DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key
                RETURNING id
                """,
                int(owner_id),
                amount,
                move_at,
                idempotency_key,
            )
        return int(move_id)

    async def expense(
        self,
        *,
        kind: str,
        category: str,
        city: str | None,
        amount: Decimal,
        expense_at: date,
        comment: str | None,
        idempotency_key: str,
    ) -> int:
        async with self._connection() as connection:
            expense_id = await connection.fetchval(
                """
                INSERT INTO expenses(
                    kind, category, city, amount, currency_code, comment,
                    expense_at, idempotency_key
                ) VALUES ($1, $2, $3, $4, 'RUB', $5, $6, $7)
                ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL
                DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key
                RETURNING id
                """,
                kind,
                category.strip(),
                city.strip().lower() if city else None,
                amount,
                comment,
                expense_at,
                idempotency_key,
            )
        return int(expense_id)
