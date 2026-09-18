from __future__ import annotations

from decimal import Decimal
from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo

from .common import clean_filter


class BalanceReadRepository(ConnectionBoundRepo):
    async def nonzero_balances(
        self,
        *,
        currency: str | None = None,
        sign: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT
                    c.id AS client_id, c.name AS client_name, c.chat_id,
                    a.currency_code, a.balance, a.precision
                FROM client_accounts a
                JOIN clients c ON c.id = a.client_id
                WHERE c.is_active
                  AND a.is_active
                  AND a.balance <> 0
                  AND NOT EXISTS (
                      SELECT 1
                      FROM cash_chat_registry cash
                      WHERE cash.client_id = a.client_id
                        AND cash.is_active
                        AND (
                            cash.cash_currency_codes IS NULL
                            OR UPPER(BTRIM(a.currency_code))
                               = ANY(cash.cash_currency_codes)
                        )
                  )
                  AND ($1::text IS NULL OR a.currency_code = UPPER($1))
                  AND ($2::text IS NULL
                       OR ($2 = '+' AND a.balance > 0)
                       OR ($2 = '-' AND a.balance < 0))
                ORDER BY a.currency_code, ABS(a.balance) DESC, c.name
                """,
                clean_filter(currency),
                clean_filter(sign),
            )
        return [dict(row) for row in rows]

    async def latest_rub_rates(self) -> dict[str, Decimal]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT DISTINCT ON (currency_code) currency_code, rate
                FROM (
                    SELECT UPPER(table_in_cur) AS currency_code, table_rate AS rate, updated_at
                    FROM exchange_request_links
                    WHERE UPPER(table_out_cur) = 'RUB' AND table_rate IS NOT NULL AND table_rate > 0
                    UNION ALL
                    SELECT UPPER(table_out_cur) AS currency_code, table_rate AS rate, updated_at
                    FROM exchange_request_links
                    WHERE UPPER(table_in_cur) = 'RUB' AND table_rate IS NOT NULL AND table_rate > 0
                ) rates
                WHERE currency_code IS NOT NULL AND currency_code <> 'RUB'
                ORDER BY currency_code, updated_at DESC
                """
            )
        rates = {"RUB": Decimal("1")}
        rates.update({str(row["currency_code"]): Decimal(str(row["rate"])) for row in rows})
        return rates
