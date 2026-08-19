from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from api.read_repositories.balances import BalanceReadRepository
from db_asyncpg.repositories.cash_chat_registry import CashChatRegistryRepo
from db_asyncpg.repositories.crm_stats import CrmStatsRepo
from domain import DomainStateError
from services.accounting.cash_chat_registry_service import CashChatRegistrySyncService


async def _deal(connection, deal_type: str) -> int:
    return int(
        await connection.fetchval(
            """
            INSERT INTO deals(deal_type, status, source)
            VALUES ($1, 'new', 'import')
            RETURNING id
            """,
            deal_type,
        )
    )


@pytest.mark.asyncio
async def test_cash_registry_allows_only_one_active_cash_per_city(pool) -> None:
    async with pool.acquire() as connection:
        first_client = await connection.fetchval(
            "INSERT INTO clients(chat_id, name) VALUES (-101, 'Касса 1') RETURNING id"
        )
        second_client = await connection.fetchval(
            "INSERT INTO clients(chat_id, name) VALUES (-102, 'Касса 2') RETURNING id"
        )
        await connection.execute(
            """
            INSERT INTO cash_chat_registry(chat_id, client_id, city, location_name)
            VALUES (-101, $1, 'екб', 'Основная')
            """,
            first_client,
        )
        await connection.execute(
            """
            INSERT INTO client_accounts(client_id, currency_code, precision, balance)
            VALUES ($1, 'RUB', 2, 1000)
            """,
            first_client,
        )
        assert await BalanceReadRepository(pool, connection=connection).nonzero_balances() == []
        assert await CrmStatsRepo(pool, connection=connection).client_balances_by_currency() == {}

        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO cash_chat_registry(chat_id, client_id, city, location_name)
                VALUES (-102, $1, ' ЕКБ ', 'Резервная')
                """,
                second_client,
            )

        await connection.execute(
            """
            UPDATE cash_chat_registry
            SET is_active = FALSE, deactivated_at = NOW()
            WHERE chat_id = -101
            """
        )
        await connection.execute(
            """
            INSERT INTO cash_chat_registry(chat_id, client_id, city, location_name)
            VALUES (-102, $1, 'екб', 'Новая основная')
            """,
            second_client,
        )

    balances = await BalanceReadRepository(pool).nonzero_balances()
    assert [(row["client_id"], row["balance"]) for row in balances] == [
        (first_client, Decimal("1000"))
    ]


@pytest.mark.asyncio
async def test_cash_registry_sync_links_existing_clients_without_moving_balances(pool) -> None:
    async with pool.acquire() as connection:
        ekb_client = await connection.fetchval(
            "INSERT INTO clients(chat_id, name) VALUES (-201, 'Касса ЕКБ') RETURNING id"
        )
        chlb_client = await connection.fetchval(
            "INSERT INTO clients(chat_id, name) VALUES (-202, 'Касса ЧЛБ') RETURNING id"
        )
        await connection.execute(
            """
            INSERT INTO client_accounts(client_id, currency_code, precision, balance)
            VALUES ($1, 'RUB', 2, 1000), ($2, 'RUB', 2, 2000)
            """,
            ekb_client,
            chlb_client,
        )

    service = CashChatRegistrySyncService(
        CashChatRegistryRepo(pool),
        city_cash_chats={"екб": -201, "члб": -202},
    )
    first = await service.sync()
    repeated = await service.sync()

    assert (first.configured, first.inserted, first.reactivated, first.deactivated) == (
        2,
        2,
        0,
        0,
    )
    assert (repeated.inserted, repeated.reactivated, repeated.deactivated) == (0, 0, 0)
    assert await BalanceReadRepository(pool).nonzero_balances() == []
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT cash.city, cash.chat_id, cash.client_id, account.balance
            FROM cash_chat_registry cash
            JOIN client_accounts account ON account.client_id = cash.client_id
            WHERE cash.is_active
            ORDER BY cash.city
            """
        )
    assert [(row["city"], row["chat_id"], row["client_id"], row["balance"]) for row in rows] == [
        ("екб", -201, ekb_client, Decimal("1000")),
        ("члб", -202, chlb_client, Decimal("2000")),
    ]

    async with pool.acquire() as connection:
        tyumen_client = await connection.fetchval(
            "INSERT INTO clients(chat_id, name) VALUES (-203, 'Касса ТЮМ') RETURNING id"
        )
        await connection.execute(
            """
            INSERT INTO client_accounts(client_id, currency_code, precision, balance)
            VALUES ($1, 'RUB', 2, 3000)
            """,
            tyumen_client,
        )
    rotation = await CashChatRegistrySyncService(
        CashChatRegistryRepo(pool),
        city_cash_chats={"тюм": -203},
    ).sync()

    assert (rotation.inserted, rotation.reactivated, rotation.deactivated) == (1, 0, 2)
    visible_balances = await BalanceReadRepository(pool).nonzero_balances()
    assert {row["client_id"] for row in visible_balances} == {ekb_client, chlb_client}


@pytest.mark.asyncio
async def test_cash_registry_sync_fails_if_configured_chat_has_no_existing_client(
    pool,
) -> None:
    service = CashChatRegistrySyncService(
        CashChatRegistryRepo(pool),
        city_cash_chats={"тюм": -999},
    )

    with pytest.raises(DomainStateError, match="тюм"):
        await service.sync()

    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT COUNT(*) FROM cash_chat_registry") == 0


@pytest.mark.asyncio
async def test_wallet_address_and_fact_snapshot_invariants(pool) -> None:
    observed_at = datetime(2026, 8, 20, 8, tzinfo=UTC)
    async with pool.acquire() as connection:
        address_id = await connection.fetchval(
            """
            INSERT INTO firm_wallet_addresses(network, address, active_from, reason)
            VALUES ('TRON', 'address-1', $1, 'opening')
            RETURNING id
            """,
            observed_at,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO firm_wallet_addresses(network, address, active_from, reason)
                VALUES (' tron ', 'address-2', $1, 'rotation')
                """,
                observed_at,
            )

        first_id = await connection.fetchval(
            """
            INSERT INTO firm_wallet_fact_snapshots(
                currency_code, address_id, actual_qty, observed_at, source, idempotency_key
            )
            VALUES ('USDT', $1, 10, $2, 'tronscan', 'snapshot:1')
            RETURNING id
            """,
            address_id,
            observed_at,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO firm_wallet_fact_snapshots(
                    currency_code, actual_qty, observed_at, source
                )
                VALUES (' usdt ', 10, $1, 'manual')
                """,
                observed_at,
            )
        await connection.execute(
            """
            INSERT INTO firm_wallet_fact_snapshots(
                currency_code, address_id, actual_qty, observed_at, source, idempotency_key
            )
            VALUES ('USDT', $1, 15, $2, 'tronscan', 'snapshot:2')
            """,
            address_id,
            observed_at + timedelta(minutes=1),
        )
        latest = await connection.fetchrow(
            """
            SELECT actual_qty, observed_at
            FROM firm_wallet_fact_snapshots
            WHERE currency_code = 'USDT'
            ORDER BY observed_at DESC, id DESC
            LIMIT 1
            """
        )

        assert latest["actual_qty"] == Decimal("15")
        assert latest["observed_at"] == observed_at + timedelta(minutes=1)
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await connection.execute(
                "UPDATE firm_wallet_fact_snapshots SET actual_qty = 20 WHERE id = $1",
                first_id,
            )


@pytest.mark.asyncio
async def test_settlement_and_queue_enforce_financial_state_contracts(pool) -> None:
    async with pool.acquire() as connection:
        sale_id = await _deal(connection, "sale")
        matched = await connection.fetchrow(
            """
            INSERT INTO deal_settlements(
                deal_id, direction, currency_code, expected_qty, actual_qty,
                review_status, idempotency_key
            )
            VALUES ($1, 'incoming', 'USDT', 500, 500, 'matched', 'settlement:1')
            RETURNING delta_qty, review_status
            """,
            sale_id,
        )

        assert matched["delta_qty"] == 0
        assert matched["review_status"] == "matched"
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO deal_settlements(
                    deal_id, direction, currency_code, expected_qty, actual_qty,
                    review_status, idempotency_key
                )
                VALUES ($1, 'incoming', 'USDT', 500, 490, 'matched', 'settlement:bad')
                """,
                sale_id,
            )

        await connection.execute(
            """
            INSERT INTO usdt_fulfillment_queue(
                deal_id, request_kind, qty, sequence_no
            )
            VALUES ($1, 'sale', 100, 10)
            """,
            sale_id,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO usdt_fulfillment_queue(
                    deal_id, request_kind, qty, sequence_no
                )
                VALUES ($1, 'client_withdrawal', 50, 20)
                """,
                sale_id,
            )


@pytest.mark.asyncio
async def test_profit_partner_watch_and_allocation_claims_are_idempotent(pool) -> None:
    now = datetime(2026, 8, 20, 8, tzinfo=UTC)
    async with pool.acquire() as connection:
        purchase_id = await _deal(connection, "purchase")
        first_sale_id = await _deal(connection, "sale")
        second_sale_id = await _deal(connection, "sale")
        await connection.execute(
            """
            INSERT INTO profit_usdt_accruals(deal_id, qty, idempotency_key)
            VALUES ($1, 5, 'profit:1')
            """,
            first_sale_id,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO profit_usdt_accruals(
                    deal_id, qty, valuation_rate, rub_value, quote_source,
                    quote_observed_at, idempotency_key
                )
                VALUES ($1, 5, 90, 450, 'manual', $2, 'profit:manual-without-audit')
                """,
                first_sale_id,
                now,
            )
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO profit_usdt_accruals(deal_id, qty, idempotency_key)
                VALUES ($1, 5, 'profit:1')
                """,
                second_sale_id,
            )

        watch_id = await connection.fetchval(
            """
            INSERT INTO partner_transfer_watches(
                purchase_deal_id, sale_deal_id, network, from_address, to_address,
                expected_qty, actual_qty, status, tx_hash, event_index, confirmed_at,
                idempotency_key
            )
            VALUES ($1, $2, 'TRON', 'partner', 'client-1', 100, 100, 'matched',
                    'ABC', 0, $3, 'watch:1')
            RETURNING id
            """,
            purchase_id,
            first_sale_id,
            now,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO partner_transfer_watches(
                    purchase_deal_id, sale_deal_id, network, from_address, to_address,
                    expected_qty, actual_qty, status, tx_hash, event_index, confirmed_at,
                    idempotency_key
                )
                VALUES ($1, $2, ' tron ', 'partner', 'client-2', 100, 100, 'matched',
                        'abc', 0, $3, 'watch:2')
                """,
                purchase_id,
                second_sale_id,
                now,
            )

        await connection.execute(
            """
            INSERT INTO partner_purchase_allocations(
                purchase_deal_id, sale_deal_id, destination_kind, qty,
                transfer_watch_id, idempotency_key
            )
            VALUES ($1, $2, 'client_direct', 100, $3, 'allocation:1')
            """,
            purchase_id,
            first_sale_id,
            watch_id,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO partner_purchase_allocations(
                    purchase_deal_id, sale_deal_id, destination_kind, qty, idempotency_key
                )
                VALUES ($1, $2, 'firm_wallet', 10, 'allocation:bad')
                """,
                purchase_id,
                second_sale_id,
            )
