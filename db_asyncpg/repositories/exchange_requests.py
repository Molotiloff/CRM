from __future__ import annotations

from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo


class ExchangeRequestsRepo(ConnectionBoundRepo):
    async def upsert_exchange_request_link(
        self,
        *,
        client_req_id: str,
        table_req_id: str,
        client_chat_id: int | None = None,
        client_message_id: int | None = None,
        request_chat_id: int | None = None,
        request_message_id: int | None = None,
        request_text: str | None = None,
        table_in_cur: str | None = None,
        table_out_cur: str | None = None,
        table_in_amount: Any | None = None,
        table_out_amount: Any | None = None,
        table_rate: Any | None = None,
        is_table_done: bool | None = None,
        status: str | None = None,
    ) -> None:
        async with self._connection() as con:
            async with con.transaction():
                await con.execute(
                    """
                    INSERT INTO exchange_request_links (
                        client_req_id,
                        table_req_id,
                        client_chat_id,
                        client_message_id,
                        request_chat_id,
                        request_message_id,
                        request_text,
                        table_in_cur,
                        table_out_cur,
                        table_in_amount,
                        table_out_amount,
                        table_rate,
                        is_table_done,
                        status,
                        updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        COALESCE($13, FALSE),
                        COALESCE($14, 'active'),
                        now()
                    )
                    ON CONFLICT (client_req_id) DO UPDATE SET
                        table_req_id = EXCLUDED.table_req_id,
                        client_chat_id = COALESCE(EXCLUDED.client_chat_id, exchange_request_links.client_chat_id),
                        client_message_id = COALESCE(EXCLUDED.client_message_id, exchange_request_links.client_message_id),
                        request_chat_id = COALESCE(EXCLUDED.request_chat_id, exchange_request_links.request_chat_id),
                        request_message_id = COALESCE(EXCLUDED.request_message_id, exchange_request_links.request_message_id),
                        request_text = COALESCE(EXCLUDED.request_text, exchange_request_links.request_text),
                        table_in_cur = COALESCE(EXCLUDED.table_in_cur, exchange_request_links.table_in_cur),
                        table_out_cur = COALESCE(EXCLUDED.table_out_cur, exchange_request_links.table_out_cur),
                        table_in_amount = COALESCE(EXCLUDED.table_in_amount, exchange_request_links.table_in_amount),
                        table_out_amount = COALESCE(EXCLUDED.table_out_amount, exchange_request_links.table_out_amount),
                        table_rate = COALESCE(EXCLUDED.table_rate, exchange_request_links.table_rate),
                        is_table_done = COALESCE($13, exchange_request_links.is_table_done),
                        status = COALESCE($14, exchange_request_links.status),
                        updated_at = now()
                    """,
                    str(client_req_id),
                    str(table_req_id),
                    int(client_chat_id) if client_chat_id is not None else None,
                    int(client_message_id) if client_message_id is not None else None,
                    int(request_chat_id) if request_chat_id is not None else None,
                    int(request_message_id) if request_message_id is not None else None,
                    request_text,
                    table_in_cur,
                    table_out_cur,
                    table_in_amount,
                    table_out_amount,
                    table_rate,
                    is_table_done,
                    status,
                )

    async def get_exchange_request_link(self, *, client_req_id: str) -> dict[str, Any] | None:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT * FROM exchange_request_links WHERE client_req_id = $1 LIMIT 1",
                    str(client_req_id),
                )
                return dict(row) if row else None

    async def get_exchange_request_link_by_table_req_id(self, *, table_req_id: str) -> dict[str, Any] | None:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT * FROM exchange_request_links WHERE table_req_id = $1 LIMIT 1",
                    str(table_req_id),
                )
                return dict(row) if row else None

    async def mark_exchange_request_table_done(self, *, table_req_id: str, is_table_done: bool = True) -> bool:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE exchange_request_links
                    SET is_table_done = $2,
                        updated_at = now()
                    WHERE table_req_id = $1
                    """,
                    str(table_req_id),
                    bool(is_table_done),
                )
                return not res.endswith(" 0")

    async def set_exchange_request_status(self, *, client_req_id: str, status: str) -> bool:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE exchange_request_links
                    SET status = $2,
                        updated_at = now()
                    WHERE client_req_id = $1
                    """,
                    str(client_req_id),
                    str(status),
                )
                return not res.endswith(" 0")
