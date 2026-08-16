from __future__ import annotations

from decimal import Decimal
from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo


class RateOrdersRepo(ConnectionBoundRepo):
    async def create_rate_order(
        self,
        *,
        client_chat_id: int,
        client_name: str,
        requested_rate: Decimal,
        created_by_user_id: int | None,
        order_chat_id: int,
        order_message_id: int,
    ) -> int:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    INSERT INTO rate_orders(
                        client_chat_id,
                        client_name,
                        requested_rate,
                        status,
                        order_chat_id,
                        order_message_id,
                        created_by_user_id,
                        updated_at
                    )
                    VALUES($1, $2, $3, 'draft', $4, $5, $6, now())
                    RETURNING id
                    """,
                    int(client_chat_id),
                    client_name,
                    requested_rate,
                    int(order_chat_id),
                    int(order_message_id),
                    created_by_user_id,
                )
                return int(row["id"])

    async def set_rate_order_message_binding(
        self,
        *,
        order_id: int,
        order_chat_id: int,
        order_message_id: int,
    ) -> None:
        async with self._connection() as con:
            async with con.transaction():
                await con.execute(
                    """
                    UPDATE rate_orders
                    SET order_chat_id = $2,
                        order_message_id = $3,
                        updated_at = now()
                    WHERE id = $1
                    """,
                    int(order_id),
                    int(order_chat_id),
                    int(order_message_id),
                )

    async def get_rate_order_by_message(
        self,
        *,
        order_chat_id: int,
        order_message_id: int,
    ) -> dict[str, Any] | None:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    SELECT *
                    FROM rate_orders
                    WHERE order_chat_id = $1
                      AND order_message_id = $2
                    LIMIT 1
                    """,
                    int(order_chat_id),
                    int(order_message_id),
                )
                return dict(row) if row else None

    async def get_rate_order_by_id(self, order_id: int) -> dict[str, Any] | None:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    SELECT *
                    FROM rate_orders
                    WHERE id = $1
                    LIMIT 1
                    """,
                    int(order_id),
                )
                return dict(row) if row else None

    async def activate_rate_order(
        self,
        *,
        order_id: int,
        commission: Decimal,
        target_ask: Decimal,
        activated_by_user_id: int | None,
    ) -> None:
        async with self._connection() as con:
            async with con.transaction():
                await con.execute(
                    """
                    UPDATE rate_orders
                    SET commission = $2,
                        target_ask = $3,
                        status = 'active',
                        activated_by_user_id = $4,
                        activated_at = now(),
                        updated_at = now()
                    WHERE id = $1
                    """,
                    int(order_id),
                    commission,
                    target_ask,
                    activated_by_user_id,
                )

    async def list_active_rate_orders(self) -> list[dict[str, Any]]:
        async with self._connection() as con:
            async with con.transaction():
                rows = await con.fetch(
                    """
                    SELECT *
                    FROM rate_orders
                    WHERE status = 'active'
                    ORDER BY target_ask ASC, id ASC
                    """
                )
                return [dict(r) for r in rows]

    async def mark_rate_order_triggered(
        self,
        *,
        order_id: int,
    ) -> bool:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE rate_orders
                    SET status = 'triggered',
                        triggered_at = now(),
                        notified_at = now(),
                        updated_at = now()
                    WHERE id = $1
                      AND status = 'active'
                    """,
                    int(order_id),
                )
                return res.endswith(" 1")
