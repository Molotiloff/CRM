from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from api.read_repositories.dashboard import DashboardReadRepository
from db_asyncpg.repositories.profit_accruals import ProfitAccrualsRepo
from db_asyncpg.uow import AsyncpgUnitOfWork
from services.accounting import FirmPositionAccountingService
from services.accounting.models import AccrueUsdtProfit, MarketQuote
from services.accounting.profit_service import (
    MidnightProfitCapitalizationJob,
    ProfitAccrualService,
    ProfitValuationService,
)


class FixedQuoteProvider:
    def __init__(self, observed_at: datetime) -> None:
        self.observed_at = observed_at

    async def best_bid(self, symbol: str) -> MarketQuote:
        return MarketQuote(
            price=Decimal("90"),
            source="rapira_ws",
            symbol=symbol,
            observed_at=self.observed_at,
        )


@pytest.mark.asyncio
async def test_midnight_capitalization_is_atomic_idempotent_and_fact_neutral(
    pool, monkeypatch
) -> None:
    business_date = date(2026, 8, 26)
    now = datetime(2026, 8, 26, 18, tzinfo=UTC)
    def factory():
        return AsyncpgUnitOfWork(pool)

    async with pool.acquire() as connection:
        deal_id = await connection.fetchval(
            """
            INSERT INTO deals(deal_type, status, source, deal_at)
            VALUES ('profit', 'done', 'crm', $1)
            RETURNING id
            """,
            business_date,
        )
        await connection.execute(
            """
            INSERT INTO firm_position_moves(
                currency_code, kind, qty, rub_amount, qty_after, rub_cost_after,
                idempotency_key, reason, effective_at
            ) VALUES ('USDT', 'opening', 10, 800, 10, 800, 'profit:opening', 'opening', $1)
            """,
            now,
        )
    accrual = await ProfitAccrualService(factory).accrue(
        AccrueUsdtProfit(
            deal_id=int(deal_id),
            qty=Decimal("5"),
            idempotency_key="profit:deal:1",
        )
    )
    repeated_accrual = await ProfitAccrualService(factory).accrue(
        AccrueUsdtProfit(
            deal_id=int(deal_id),
            qty=Decimal("5"),
            idempotency_key="profit:deal:1",
        )
    )
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE profit_usdt_accruals SET created_at = $2 WHERE id = $1",
            accrual.id,
            now,
        )
    assert repeated_accrual.id == accrual.id
    before = await DashboardReadRepository(pool).get_snapshot(business_date=business_date)
    before_usdt = next(item for item in before.currencies if item.code == "USDT")

    job = MidnightProfitCapitalizationJob(
        factory,
        valuation_service=ProfitValuationService(
            FixedQuoteProvider(now),
            now_factory=lambda: now,
        ),
        position_service=FirmPositionAccountingService(factory),
        now_factory=lambda: now,
    )
    original_mark = ProfitAccrualsRepo.mark_capitalized

    async def fail_mark(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("capitalization mark failed")

    monkeypatch.setattr(ProfitAccrualsRepo, "mark_capitalized", fail_mark)
    with pytest.raises(RuntimeError, match="capitalization mark failed"):
        await job.run(business_date)
    async with pool.acquire() as connection:
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM firm_position_moves WHERE kind = 'profit_capitalization'"
        ) == 0
        assert await connection.fetchval(
            "SELECT capitalization_status FROM profit_usdt_accruals WHERE id = $1",
            accrual.id,
        ) == "pending"
    monkeypatch.setattr(ProfitAccrualsRepo, "mark_capitalized", original_mark)

    result = await job.run(business_date)
    repeated = await job.run(business_date)
    after = await DashboardReadRepository(pool).get_snapshot(business_date=business_date)
    after_usdt = next(item for item in after.currencies if item.code == "USDT")

    assert (result.qty, result.rub_value, result.accrual_count) == (
        Decimal("5"),
        Decimal("450"),
        1,
    )
    assert repeated.repeated is True
    assert before_usdt.fact_qty == after_usdt.fact_qty == Decimal("15.00000000")
    assert (after_usdt.free_qty, after_usdt.deal_profit_qty) == (
        Decimal("15.00000000"),
        Decimal("0"),
    )
    async with pool.acquire() as connection:
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM firm_position_moves WHERE kind = 'profit_capitalization'"
        ) == 1
        row = await connection.fetchrow(
            """
            SELECT capitalization_status, settlement_status, valuation_rate,
                   quote_source, capitalization_move_id
            FROM profit_usdt_accruals WHERE id = $1
            """,
            accrual.id,
        )
    assert (
        row["capitalization_status"],
        row["settlement_status"],
        row["valuation_rate"],
        row["quote_source"],
    ) == ("capitalized", "in_transit", Decimal("90.00000000"), "rapira_ws")
    assert row["capitalization_move_id"] == result.position_move_id
