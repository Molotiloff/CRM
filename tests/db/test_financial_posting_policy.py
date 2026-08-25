from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from db_asyncpg.repositories.crm_stats import CrmStatsRepo
from domain import DealStatus


@pytest.mark.asyncio
async def test_only_done_deals_are_in_financial_reports_and_typed_views(pool) -> None:
    report_day = date(2026, 8, 19)
    done_profit = Decimal(tuple(DealStatus).index(DealStatus.DONE) + 1)
    async with pool.acquire() as connection:
        for index, status in enumerate(DealStatus, start=1):
            await connection.execute(
                """
                INSERT INTO deals(deal_type, city, status, source, body, profit, deal_at)
                VALUES (
                    'sale', 'Екб', $1, 'import',
                    jsonb_build_object(
                        'currency', 'USDT',
                        'qty', 1,
                        'entry_rate', 90,
                        'exit_rate', 100,
                        'buy_amount', 90,
                        'sale_amount', 100,
                        'kt_spread', 0,
                        'kt_amount', 0,
                        'our_profit', $2::numeric
                    ),
                    $2,
                    $3
                )
                """,
                status.value,
                Decimal(index),
                report_day,
            )

        view_rows = await connection.fetch("SELECT status, profit FROM crm_sales ORDER BY id")

    assert [(row["status"], row["profit"]) for row in view_rows] == [
        (DealStatus.DONE.value, done_profit)
    ]

    pnl = await CrmStatsRepo(pool).daily_pnl(report_day, report_day)

    assert len(pnl) == 1
    assert pnl[0]["sales_income"] == done_profit
    assert pnl[0]["deals_income"] == 0
