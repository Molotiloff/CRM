from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from api.read_repositories.dashboard import DashboardReadRepository


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
            "INSERT INTO internal_accounts(name, balance) VALUES ('Внутренний', -100)"
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
                    '{"sale_amount": "10000"}'::jsonb)
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
        Decimal("10.00000000"),
        Decimal("5.00000000"),
        Decimal("115.00000000"),
    )
    assert (usdt.observed_qty, usdt.gap) == (
        Decimal("120.00000000"),
        Decimal("5.00000000"),
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
    assert snapshot.periods.monthly_turnover == Decimal("10000")
    assert snapshot.periods.profitability == Decimal("0.03")
    assert snapshot.cities[0].profit == Decimal("250.00000000")
    assert snapshot.operations.active_requests == 2
    assert snapshot.operations.clients_with_balance == 1
    assert snapshot.operations.queue_shortage_qty == 0
    assert snapshot.operations.onchain_liquid_qty == Decimal("120.00000000")
    assert snapshot.warnings == (
        "Client FX balances are excluded from RUB valuation (stored_rub_only)",
        "USDT physical balance is stale",
    )


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
