from __future__ import annotations

from decimal import Decimal
from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo


class DashboardReadRepository(ConnectionBoundRepo):
    async def cash_desks_balances(self) -> list[dict[str, Any]]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT city, currency_code, SUM(balance) AS balance
                FROM cash_desks
                WHERE is_active
                GROUP BY city, currency_code
                ORDER BY city, currency_code
                """
            )
        return [dict(row) for row in rows]

    async def active_schedule_counts(self) -> list[dict[str, Any]]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT city, COUNT(*) AS active_count
                FROM request_schedule_entries
                WHERE is_active
                GROUP BY city
                ORDER BY city
                """
            )
        return [dict(row) for row in rows]

    async def active_exchange_request_count(self) -> int:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT COUNT(*) AS active_count
                FROM exchange_request_links
                WHERE status = 'active'
                """
            )
        return int(row["active_count"] if row else 0)

    async def today_expenses_total(self) -> Decimal:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM expenses
                WHERE expense_at = CURRENT_DATE
                """
            )
        return Decimal(str(row["total"] if row else 0))
