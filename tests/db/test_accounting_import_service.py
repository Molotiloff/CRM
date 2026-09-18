from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from db_asyncpg.uow import AsyncpgUnitOfWork
from services.accounting.firm_position_service import FirmPositionAccountingService
from services.accounting.import_models import AccountingImportManifest
from services.accounting.import_service import AccountingImportService


def _service(pool) -> AccountingImportService:
    def factory():
        return AsyncpgUnitOfWork(pool)

    return AccountingImportService(
        factory,
        position_service=FirmPositionAccountingService(factory),
    )


@pytest.mark.asyncio
async def test_import_dry_run_writes_nothing_and_apply_is_idempotent(pool) -> None:
    async with pool.acquire() as connection:
        cash_id = await connection.fetchval(
            "INSERT INTO clients(chat_id, name) VALUES (-801, 'Import cash') RETURNING id"
        )
        client_id = await connection.fetchval(
            "INSERT INTO clients(chat_id, name) VALUES (-802, 'Import client') RETURNING id"
        )
        await connection.execute(
            """
            INSERT INTO client_accounts(client_id, currency_code, precision, balance)
            VALUES ($1, 'RUB', 2, 0), ($2, 'USDT', 8, 0)
            """,
            cash_id,
            client_id,
        )
    manifest = _manifest()
    service = _service(pool)

    preview = await service.run(manifest, dry_run=True)
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT COUNT(*) FROM accounting_import_runs") == 0
        assert await connection.fetchval("SELECT COUNT(*) FROM transactions") == 0

    first = await service.run(manifest)
    repeated = await service.run(manifest)

    assert preview.status == "validated"
    assert (first.applied_count, first.repeated_count, first.checkpoint) == (11, 0, 11)
    assert (repeated.applied_count, repeated.repeated_count) == (0, 11)
    async with pool.acquire() as connection:
        counts = await connection.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM accounting_import_runs) AS runs,
                (SELECT COUNT(*) FROM accounting_import_records) AS records,
                (SELECT COUNT(*) FROM transactions) AS transactions,
                (SELECT COUNT(*) FROM internal_account_moves) AS internal_moves,
                (SELECT COUNT(*) FROM capital_moves) AS capital_moves,
                (SELECT COUNT(*) FROM expenses) AS expenses,
                (SELECT COUNT(*) FROM firm_position_moves) AS position_moves,
                (SELECT COUNT(*) FROM firm_wallet_fact_snapshots) AS wallet_facts,
                (SELECT COUNT(*) FROM profit_usdt_accruals) AS profit_accruals,
                (SELECT COUNT(*) FROM deals) AS deals
            """
        )
        balances = await connection.fetch(
            "SELECT currency_code, balance FROM client_accounts ORDER BY currency_code"
        )
        internal_balances = await connection.fetch(
            "SELECT currency_code, balance FROM internal_accounts ORDER BY currency_code"
        )
    assert tuple(counts) == (1, 11, 2, 2, 1, 1, 1, 1, 1, 1)
    assert [(row["currency_code"], row["balance"]) for row in balances] == [
        ("RUB", Decimal("1000.00")),
        ("USDT", Decimal("5.00000000")),
    ]
    assert [(row["currency_code"], row["balance"]) for row in internal_balances] == [
        ("RUB", Decimal("-50.00000000")),
        ("USDT", Decimal("7.50000000")),
    ]


@pytest.mark.asyncio
async def test_import_resumes_from_committed_checkpoint(pool, monkeypatch) -> None:
    manifest = AccountingImportManifest.from_dict(
        {
            "sourceName": "restart-test",
            "cutoverAt": "2026-08-26T00:00:00+05:00",
            "positionStrategy": {},
            "records": [
                {
                    "key": "expense-1",
                    "kind": "expense",
                    "payload": {
                        "expenseKind": "variable",
                        "category": "One",
                        "amount": "10",
                        "date": "2026-08-26",
                    },
                },
                {
                    "key": "expense-2",
                    "kind": "expense",
                    "payload": {
                        "expenseKind": "variable",
                        "category": "Two",
                        "amount": "20",
                        "date": "2026-08-26",
                    },
                },
            ],
            "expectedTotals": {"expense.RUB.amount": "30"},
        }
    )
    service = _service(pool)
    original = service._apply_record

    async def fail_second(unit_of_work, *, manifest, record):
        if record.key == "expense-2":
            raise RuntimeError("interrupted import")
        return await original(unit_of_work, manifest=manifest, record=record)

    monkeypatch.setattr(service, "_apply_record", fail_second)
    with pytest.raises(RuntimeError, match="interrupted import"):
        await service.run(manifest)
    async with pool.acquire() as connection:
        run = await connection.fetchrow(
            "SELECT status, checkpoint FROM accounting_import_runs"
        )
        assert (run["status"], run["checkpoint"]) == ("failed", 1)
        assert await connection.fetchval("SELECT COUNT(*) FROM expenses") == 1

    monkeypatch.setattr(service, "_apply_record", original)
    result = await service.run(manifest)
    assert (result.applied_count, result.repeated_count, result.checkpoint) == (1, 1, 2)
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT COUNT(*) FROM expenses") == 2


def _manifest() -> AccountingImportManifest:
    records = [
        {
            "key": "cash-registry",
            "kind": "cash_registry",
            "payload": {"chatId": -801, "city": "екб", "locationName": "Main"},
        },
        {
            "key": "cash-rub",
            "kind": "cash_balance",
            "payload": {"chatId": -801, "currency": "RUB", "amount": "1000"},
        },
        {
            "key": "client-usdt",
            "kind": "client_balance",
            "payload": {"chatId": -802, "currency": "USDT", "amount": "5"},
        },
        {
            "key": "internal",
            "kind": "internal_balance",
            "payload": {"name": "Import internal", "accountKind": "tech", "amount": "-50"},
        },
        {
            "key": "internal-usdt",
            "kind": "internal_balance",
            "payload": {
                "name": "Import USDT adjustment",
                "accountKind": "tech",
                "currency": "USDT",
                "amount": "7.5",
            },
        },
        {
            "key": "capital",
            "kind": "capital",
            "payload": {"owner": "Import owner", "amount": "500", "date": "2026-08-26"},
        },
        {
            "key": "expense",
            "kind": "expense",
            "payload": {
                "expenseKind": "variable",
                "category": "Import",
                "amount": "20",
                "date": "2026-08-26",
            },
        },
        {
            "key": "position",
            "kind": "position_opening",
            "payload": {"currency": "USDT", "qty": "10", "rubCost": "800"},
        },
        {
            "key": "wallet",
            "kind": "wallet_fact",
            "payload": {"currency": "USDT", "qty": "15"},
        },
        {
            "key": "profit-deal",
            "kind": "deal",
            "payload": {
                "dealType": "profit",
                "city": "другое",
                "date": "2026-08-26",
                "profit": "160",
                "body": {"currency": "USDT", "qty": "2"},
            },
        },
        {
            "key": "profit",
            "kind": "profit_accrual",
            "payload": {"dealKey": "profit-deal", "qty": "2"},
        },
    ]
    return AccountingImportManifest.from_dict(
        {
            "sourceName": "stage-8-test",
            "cutoverAt": datetime(2026, 8, 26, tzinfo=UTC).isoformat(),
            "positionStrategy": {"USDT": "opening"},
            "records": records,
            "expectedTotals": {
                "cash_balance.RUB.amount": "1000",
                "client_balance.USDT.amount": "5",
                "internal_balance.RUB.amount": "-50",
                "internal_balance.USDT.amount": "7.5",
                "capital.RUB.amount": "500",
                "expense.RUB.amount": "20",
                "position_opening.USDT.qty": "10",
                "position_opening.USDT.rub": "800",
                "wallet_fact.USDT.qty": "15",
                "profit_accrual.USDT.qty": "2",
                "deal.RUB.profit": "160",
            },
        }
    )
