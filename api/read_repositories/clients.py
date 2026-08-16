from __future__ import annotations

from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo

from .common import clean_filter


class ClientReadRepository(ConnectionBoundRepo):
    async def count_clients(self, *, search: str | None = None, group: str | None = None) -> int:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT COUNT(*) AS total
                FROM clients c
                WHERE c.is_active
                  AND ($1::text IS NULL OR c.name ILIKE '%' || $1 || '%'
                       OR c.chat_id::text ILIKE '%' || $1 || '%'
                       OR COALESCE(c.client_group, '') ILIKE '%' || $1 || '%')
                  AND ($2::text IS NULL OR LOWER(COALESCE(c.client_group, '')) = LOWER($2))
                """,
                clean_filter(search),
                clean_filter(group),
            )
        return int(row["total"] if row else 0)

    async def list_clients(
        self,
        *,
        search: str | None = None,
        group: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT c.id, c.chat_id, c.name, c.client_group, c.created_at
                FROM clients c
                WHERE c.is_active
                  AND ($1::text IS NULL OR c.name ILIKE '%' || $1 || '%'
                       OR c.chat_id::text ILIKE '%' || $1 || '%'
                       OR COALESCE(c.client_group, '') ILIKE '%' || $1 || '%')
                  AND ($2::text IS NULL OR LOWER(COALESCE(c.client_group, '')) = LOWER($2))
                ORDER BY c.created_at DESC, c.id DESC
                LIMIT $3 OFFSET $4
                """,
                clean_filter(search),
                clean_filter(group),
                limit,
                offset,
            )
        return [dict(row) for row in rows]

    async def get_client(self, client_id: int) -> dict[str, Any] | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT c.id, c.chat_id, c.name, c.client_group, c.created_at
                FROM clients c
                WHERE c.id = $1 AND c.is_active
                """,
                client_id,
            )
        return dict(row) if row else None

    async def balances_for_clients(self, client_ids: list[int]) -> list[dict[str, Any]]:
        if not client_ids:
            return []
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT client_id, currency_code, balance, precision
                FROM client_accounts
                WHERE client_id = ANY($1::bigint[]) AND is_active
                ORDER BY client_id, currency_code
                """,
                client_ids,
            )
        return [dict(row) for row in rows]

    async def stats_for_clients(self, client_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not client_ids:
            return {}
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT
                    t.client_id,
                    COUNT(*) AS deals_count,
                    COALESCE(SUM(ABS(t.amount)) FILTER (WHERE a.currency_code = 'RUB'), 0) AS turnover_rub,
                    COALESCE(SUM(ABS(t.amount)) FILTER (WHERE a.currency_code = 'RUB' AND t.amount < 0), 0) AS purchase_volume_rub,
                    COALESCE(SUM(ABS(t.amount)) FILTER (WHERE a.currency_code = 'RUB' AND t.amount > 0), 0) AS sale_volume_rub
                FROM transactions t
                JOIN client_accounts a ON a.id = t.account_id
                WHERE t.client_id = ANY($1::bigint[])
                GROUP BY t.client_id
                """,
                client_ids,
            )
        return {int(row["client_id"]): dict(row) for row in rows}

    async def recent_transactions_by_client(
        self, client_ids: list[int], *, limit_per_client: int = 3
    ) -> dict[int, list[dict[str, Any]]]:
        if not client_ids:
            return {}
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT *
                FROM (
                    SELECT
                        t.client_id, t.id, t.txn_at, t.amount, t.balance_after,
                        t.comment, t.source, a.currency_code,
                        ROW_NUMBER() OVER (
                            PARTITION BY t.client_id ORDER BY t.txn_at DESC, t.id DESC
                        ) AS rn
                    FROM transactions t
                    JOIN client_accounts a ON a.id = t.account_id
                    WHERE t.client_id = ANY($1::bigint[])
                ) q
                WHERE q.rn <= $2
                ORDER BY q.client_id, q.txn_at DESC, q.id DESC
                """,
                client_ids,
                limit_per_client,
            )
        grouped: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(int(row["client_id"]), []).append(dict(row))
        return grouped

    async def client_transactions(
        self, client_id: int, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT
                    t.id, t.txn_at, t.amount, t.balance_after, t.comment, t.source,
                    a.currency_code, g.name AS group_name, ac.display_name AS actor_name
                FROM transactions t
                JOIN client_accounts a ON a.id = t.account_id
                LEFT JOIN txn_groups g ON g.id = t.group_id
                LEFT JOIN actors ac ON ac.id = t.actor_id
                WHERE t.client_id = $1
                ORDER BY t.txn_at DESC, t.id DESC
                LIMIT $2
                """,
                client_id,
                limit,
            )
        return [dict(row) for row in rows]
