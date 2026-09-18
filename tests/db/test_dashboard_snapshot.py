from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from api.read_repositories.dashboard import DashboardReadRepository
from db_asyncpg.repositories.shadow_comparisons import ShadowComparisonsRepo
from services.accounting.dashboard_comparison_service import DashboardComparisonService


@pytest.mark.asyncio
async def test_dashboard_snapshot_uses_canonical_postgres_sources_and_formulas(pool) -> None:
    business_date = date(2026, 8, 25)
    observed_at = datetime(2026, 8, 24, 8, tzinfo=UTC)
    async with pool.acquire() as connection:
        cash_client = await connection.fetchval(
            "INSERT INTO clients(chat_id, name) VALUES (-401, 'Касса ЕКБ') RETURNING id"
        )
        client = await connection.fetchval(
            "INSERT INTO clients(chat_id, name) VALUES (-402, 'Клиент') RETURNING id"
        )
        internal_wallet = await connection.fetchval(
            """
            INSERT INTO clients(chat_id, name, client_group)
            VALUES (-403, 'Операционный кошелек', 'internal_wallet')
            RETURNING id
            """
        )
        await connection.execute(
            """
            INSERT INTO client_accounts(client_id, currency_code, precision, balance)
            VALUES ($1, 'RUB', 2, 1000), ($1, 'USD', 2, 50),
                   ($2, 'RUB', 2, -200), ($2, 'USDT', 2, 10)
            """,
            cash_client,
            client,
        )
        await connection.execute(
            """
            INSERT INTO client_accounts(client_id, currency_code, precision, balance)
            VALUES ($1, 'RUB', 2, 999), ($1, 'USDT', 8, 777)
            """,
            internal_wallet,
        )
        await connection.execute(
            """
            INSERT INTO cash_chat_registry(chat_id, client_id, city, location_name)
            VALUES (-401, $1, 'екб', 'Основная')
            """,
            cash_client,
        )
        await connection.execute(
            """
            INSERT INTO firm_position_moves(
                currency_code, kind, qty, rub_amount, qty_after, rub_cost_after,
                idempotency_key, reason, effective_at
            ) VALUES
                ('USDT', 'opening', 100, 8000, 100, 8000,
                 'dashboard:usdt', 'opening', NOW()),
                ('USD_BL', 'opening', 50, 4500, 50, 4500,
                 'dashboard:usd', 'opening', NOW())
            """
        )
        await connection.execute(
            """
            INSERT INTO internal_accounts(name, currency_code, balance)
            VALUES ('Внутренний', 'RUB', -100),
                   ('USDT legacy adjustment', 'USDT', 7)
            """
        )
        owner_id = await connection.fetchval(
            "INSERT INTO capital_owners(name) VALUES ('Владелец') RETURNING id"
        )
        await connection.execute(
            "INSERT INTO capital_moves(owner_id, amount, move_at) VALUES ($1, 5000, $2)",
            owner_id,
            business_date,
        )
        await connection.execute(
            """
            INSERT INTO expenses(kind, category, city, amount, expense_at)
            VALUES ('variable', 'Тест', 'екб', 50, $1)
            """,
            business_date,
        )
        done_deal = await connection.fetchval(
            """
            INSERT INTO deals(deal_type, city, status, source, profit, deal_at, body)
            VALUES ('sale', 'екб', 'done', 'import', 300, $1,
                    '{"sale_amount": "10000", "rub_cost": "9000"}'::jsonb)
            RETURNING id
            """,
            business_date,
        )
        await connection.execute(
            """
            INSERT INTO deals(deal_type, city, status, source, profit, deal_at)
            VALUES ('sale', 'екб', 'new', 'import', 999, $1)
            """,
            business_date,
        )
        await connection.execute(
            """
            INSERT INTO usdt_fulfillment_queue(deal_id, request_kind, qty, sequence_no)
            VALUES ($1, 'sale', 20, 1)
            """,
            done_deal,
        )
        await connection.execute(
            """
            INSERT INTO profit_usdt_accruals(
                deal_id, qty, idempotency_key, capitalization_status, settlement_status
            ) VALUES ($1, 5, 'dashboard:profit', 'pending', 'in_transit')
            """,
            done_deal,
        )
        await connection.execute(
            """
            INSERT INTO firm_wallet_fact_snapshots(
                currency_code, actual_qty, observed_at, source, idempotency_key
            ) VALUES ('USDT', 120, $1, 'import', 'dashboard:wallet')
            """,
            observed_at,
        )
        await connection.execute(
            """
            INSERT INTO request_schedule_entries(
                req_id, city, request_kind, line_text, client_name,
                request_chat_id, request_message_id
            ) VALUES ('dashboard:schedule', 'екб', 'deposit', 'line', 'client', -1, 1);
            INSERT INTO exchange_request_links(client_req_id, table_req_id)
            VALUES ('dashboard:exchange', 'dashboard:exchange');
            INSERT INTO cash_desks(city, name, currency_code, balance)
            VALUES ('legacy', 'ignored', 'RUB', 999999);
            INSERT INTO firm_wallet_facts(currency_code, actual_qty)
            VALUES ('USDT', 999999);
            """
        )

    snapshot = await DashboardReadRepository(pool).get_snapshot(
        business_date=business_date
    )

    assert snapshot.source == "postgres"
    assert [item.code for item in snapshot.currencies] == [
        "EUR",
        "USDT",
        "USD_BL",
        "USD_WH",
    ]
    assert "CNY" not in {item.code for item in snapshot.currencies}
    usdt = next(item for item in snapshot.currencies if item.code == "USDT")
    assert (usdt.free_qty, usdt.internal_rate, usdt.rub_cost) == (
        Decimal("100.00000000"),
        Decimal("80"),
        Decimal("8000.00000000"),
    )
    assert (usdt.client_qty, usdt.deal_profit_qty, usdt.fact_qty) == (
        Decimal("17.00000000"),
        Decimal("5.00000000"),
        Decimal("122.00000000"),
    )
    assert (usdt.observed_qty, usdt.gap) == (
        Decimal("120.00000000"),
        Decimal("-2.00000000"),
    )
    reconciliation = snapshot.reconciliation
    assert reconciliation.rub_cash == Decimal("1000.00000000")
    assert reconciliation.rub_in_currency == Decimal("12500.00000000")
    assert reconciliation.total_balances == Decimal("-300.00000000")
    assert reconciliation.fact_turnover == Decimal("13800.00000000")
    assert reconciliation.accumulated_profit == Decimal("250.00000000")
    assert reconciliation.turnover == Decimal("5250.00000000")
    assert reconciliation.gap == Decimal("8550.00000000")
    assert reconciliation.fact_rub == Decimal("13200.00000000")
    assert snapshot.periods.daily_profit == Decimal("250.00000000")
    assert snapshot.periods.daily_turnover == Decimal("10000")
    assert snapshot.periods.monthly_turnover == Decimal("9000")
    assert snapshot.periods.profitability == Decimal(300) / Decimal(9000)
    assert snapshot.cities[0].profit == Decimal("250.00000000")
    assert snapshot.operations.active_requests == 2
    assert snapshot.operations.clients_with_balance == 1
    assert snapshot.operations.queue_shortage_qty == 0
    assert snapshot.operations.onchain_liquid_qty == Decimal("120.00000000")
    assert snapshot.warnings == (
        "Client FX balances are excluded from RUB valuation (stored_rub_only)",
        "USDT physical balance is stale",
    )


@pytest.mark.asyncio
async def test_dashboard_snapshot_normalizes_city_aliases(pool) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO expenses(kind, category, city, amount, expense_at)
            VALUES ('variable', 'One', 'тюм', 10, '2026-08-26'),
                   ('variable', 'Two', 'Тюмень', 20, '2026-08-26');
            INSERT INTO deals(deal_type, city, status, source, profit, deal_at)
            VALUES ('profit', 'Москва', 'done', 'import', 30, '2026-08-26'),
                   ('profit', 'мск', 'done', 'import', 40, '2026-08-26');
            """
        )

    snapshot = await DashboardReadRepository(pool).get_snapshot(
        business_date=date(2026, 8, 26)
    )
    cities = {item.city: item for item in snapshot.cities}

    assert cities["тюм"].expense == Decimal("30.00000000")
    assert cities["мск"].income == Decimal("70.00000000")
    assert "тюмень" not in cities
    assert "москва" not in cities


@pytest.mark.asyncio
async def test_dashboard_includes_only_manual_moscow_cash_accounts(pool) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO cash_desks(city, name, currency_code, balance)
            VALUES ('мск', 'Поэты', 'RUB', -57565),
                   ('мск', 'BS', 'RUB', 82408),
                   ('legacy', 'ignored', 'RUB', 999999)
            """
        )

    snapshot = await DashboardReadRepository(pool).get_snapshot(
        business_date=date(2026, 8, 26)
    )

    assert snapshot.reconciliation.rub_cash == Decimal("24843.00000000")


@pytest.mark.asyncio
async def test_shadow_comparison_report_is_persisted_with_diagnostics(pool) -> None:
    business_date = date(2026, 8, 26)
    database = await DashboardReadRepository(pool).get_snapshot(
        business_date=business_date
    )
    sheets = replace(
        database,
        source="sheets",
        warnings=(),
        reconciliation=replace(
            database.reconciliation,
            total_rub=database.reconciliation.total_rub + Decimal("10"),
        ),
    )
    report = DashboardComparisonService().compare(sheets=sheets, database=database)

    report_id = await ShadowComparisonsRepo(pool).save(
        business_date=business_date,
        primary_source="sheets",
        report=report,
    )

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT status, mismatch_count, diagnostics
            FROM dashboard_shadow_reports WHERE id = $1
            """,
            report_id,
        )
    assert row["status"] == "mismatched"
    assert row["mismatch_count"] >= 1
    diagnostics = json.loads(row["diagnostics"])
    assert any(item["path"] == "reconciliation.total_rub" for item in diagnostics)

    stored = await ShadowComparisonsRepo(pool).get(report_id)
    assert stored is not None
    assert stored.id == report_id
    assert stored.business_date == business_date
    assert any(
        item.path == "reconciliation.total_rub"
        and item.sheet_value == database.reconciliation.total_rub + Decimal("10")
        and item.db_value == database.reconciliation.total_rub
        for item in stored.differences
    )


@pytest.mark.asyncio
async def test_identical_shadow_comparison_reuses_persisted_report(pool) -> None:
    business_date = date(2026, 8, 26)
    database = await DashboardReadRepository(pool).get_snapshot(
        business_date=business_date
    )
    sheets = replace(database, source="sheets", warnings=())
    report = DashboardComparisonService().compare(sheets=sheets, database=database)
    repository = ShadowComparisonsRepo(pool)

    first_id = await repository.save(
        business_date=business_date,
        primary_source="sheets",
        report=report,
    )
    second_id = await repository.save(
        business_date=business_date,
        primary_source="sheets",
        report=report,
    )

    assert second_id == first_id
    async with pool.acquire() as connection:
        count = await connection.fetchval(
            "SELECT COUNT(*) FROM dashboard_shadow_reports"
        )
        fingerprint = await connection.fetchval(
            "SELECT report_fingerprint FROM dashboard_shadow_reports WHERE id = $1",
            first_id,
        )
    assert count == 1
    assert len(fingerprint) == 32


@pytest.mark.asyncio
async def test_changed_shadow_comparison_creates_new_report(pool) -> None:
    business_date = date(2026, 8, 26)
    database = await DashboardReadRepository(pool).get_snapshot(
        business_date=business_date
    )
    repository = ShadowComparisonsRepo(pool)
    first = DashboardComparisonService().compare(
        sheets=replace(database, source="sheets", warnings=()),
        database=database,
    )
    changed_sheets = replace(
        database,
        source="sheets",
        warnings=(),
        reconciliation=replace(
            database.reconciliation,
            total_rub=database.reconciliation.total_rub + Decimal("1"),
        ),
    )
    changed = DashboardComparisonService().compare(
        sheets=changed_sheets,
        database=database,
    )

    first_id = await repository.save(
        business_date=business_date,
        primary_source="sheets",
        report=first,
    )
    changed_id = await repository.save(
        business_date=business_date,
        primary_source="sheets",
        report=changed,
    )

    assert changed_id != first_id


class PausingDashboardReadRepository(DashboardReadRepository):
    def __init__(self, pool, *, started: asyncio.Event, proceed: asyncio.Event) -> None:
        super().__init__(pool)
        self._started = started
        self._proceed = proceed

    async def _after_snapshot_started(self) -> None:
        self._started.set()
        await self._proceed.wait()


@pytest.mark.asyncio
async def test_dashboard_snapshot_is_consistent_during_concurrent_write(pool) -> None:
    async with pool.acquire() as connection:
        client_id = await connection.fetchval(
            "INSERT INTO clients(chat_id, name) VALUES (-403, 'Concurrent') RETURNING id"
        )
        await connection.execute(
            """
            INSERT INTO client_accounts(client_id, currency_code, precision, balance)
            VALUES ($1, 'RUB', 2, 100)
            """,
            client_id,
        )

    started = asyncio.Event()
    proceed = asyncio.Event()
    repository = PausingDashboardReadRepository(
        pool,
        started=started,
        proceed=proceed,
    )
    snapshot_task = asyncio.create_task(
        repository.get_snapshot(business_date=date(2026, 8, 25))
    )
    await started.wait()
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE client_accounts SET balance = 999
            WHERE client_id = $1 AND currency_code = 'RUB'
            """,
            client_id,
        )
    proceed.set()

    snapshot = await snapshot_task

    assert snapshot.reconciliation.client_balances == Decimal("100.00000000")
