from __future__ import annotations

from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo


class RequestScheduleRepo(ConnectionBoundRepo):
    async def next_request_id(self) -> int:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow("SELECT nextval('request_id_seq') AS value")
                return int(row["value"])

    async def upsert_request_schedule_entry(
        self,
        *,
        req_id: str,
        city: str,
        hhmm: str | None,
        request_kind: str,
        line_text: str,
        client_name: str,
        request_chat_id: int,
        request_message_id: int,
    ) -> None:
        async with self._connection() as con:
            async with con.transaction():
                await con.execute(
                    """
                    INSERT INTO request_schedule_entries (
                        req_id,
                        city,
                        hhmm,
                        request_kind,
                        line_text,
                        client_name,
                        request_chat_id,
                        request_message_id,
                        is_active,
                        updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE, now())
                    ON CONFLICT (req_id) DO UPDATE SET
                        city = EXCLUDED.city,
                        hhmm = EXCLUDED.hhmm,
                        request_kind = EXCLUDED.request_kind,
                        line_text = EXCLUDED.line_text,
                        client_name = EXCLUDED.client_name,
                        request_chat_id = EXCLUDED.request_chat_id,
                        request_message_id = EXCLUDED.request_message_id,
                        is_active = TRUE,
                        updated_at = now()
                    """,
                    req_id,
                    city.strip().lower(),
                    (hhmm or None),
                    request_kind,
                    line_text,
                    client_name,
                    int(request_chat_id),
                    int(request_message_id),
                )

    async def list_request_schedule_entries(
        self,
        *,
        city: str,
    ) -> list[dict[str, Any]]:
        async with self._connection() as con:
            async with con.transaction():
                rows = await con.fetch(
                    """
                    SELECT
                        req_id,
                        city,
                        hhmm,
                        request_kind,
                        line_text,
                        client_name,
                        request_chat_id,
                        request_message_id,
                        is_active,
                        created_at,
                        updated_at
                    FROM request_schedule_entries
                    WHERE city = $1
                      AND is_active = TRUE
                    ORDER BY hhmm ASC, updated_at ASC, id ASC
                    """,
                    city.strip().lower(),
                )
                return [dict(r) for r in rows]

    async def deactivate_request_schedule_entry(self, req_id: str) -> bool:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE request_schedule_entries
                    SET is_active = FALSE,
                        updated_at = now()
                    WHERE req_id = $1
                      AND is_active = TRUE
                    """,
                    req_id,
                )
                return res.endswith(" 1")

    async def get_request_schedule_board(self, *, city: str) -> dict[str, Any] | None:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    SELECT city, board_chat_id, board_message_id, updated_at
                    FROM request_schedule_boards
                    WHERE city = $1
                    """,
                    city.strip().lower(),
                )
                return dict(row) if row else None

    async def upsert_request_schedule_board(
        self,
        *,
        city: str,
        board_chat_id: int,
        board_message_id: int,
    ) -> None:
        async with self._connection() as con:
            async with con.transaction():
                await con.execute(
                    """
                    INSERT INTO request_schedule_boards (
                        city,
                        board_chat_id,
                        board_message_id,
                        updated_at
                    )
                    VALUES ($1, $2, $3, now())
                    ON CONFLICT (city) DO UPDATE SET
                        board_chat_id = EXCLUDED.board_chat_id,
                        board_message_id = EXCLUDED.board_message_id,
                        updated_at = now()
                    """,
                    city.strip().lower(),
                    int(board_chat_id),
                    int(board_message_id),
                )

    async def deactivate_request_schedule_entry_by_message(
        self,
        *,
        request_chat_id: int,
        request_message_id: int,
    ) -> bool:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE request_schedule_entries
                    SET is_active = FALSE,
                        updated_at = now()
                    WHERE request_chat_id = $1
                      AND request_message_id = $2
                      AND is_active = TRUE
                    """,
                    int(request_chat_id),
                    int(request_message_id),
                )
                return not res.endswith(" 0")

    async def get_request_schedule_entry_by_message(
        self,
        *,
        request_chat_id: int,
        request_message_id: int,
    ) -> dict[str, Any] | None:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    SELECT
                        req_id,
                        city,
                        hhmm,
                        request_kind,
                        line_text,
                        client_name,
                        request_chat_id,
                        request_message_id,
                        is_active,
                        created_at,
                        updated_at
                    FROM request_schedule_entries
                    WHERE request_chat_id = $1
                      AND request_message_id = $2
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                    """,
                    int(request_chat_id),
                    int(request_message_id),
                )
                return dict(row) if row else None

    async def get_request_schedule_entry_by_req_id(
        self,
        *,
        req_id: str,
    ) -> dict[str, Any] | None:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    SELECT
                        req_id,
                        city,
                        hhmm,
                        request_kind,
                        line_text,
                        client_name,
                        request_chat_id,
                        request_message_id,
                        is_active,
                        created_at,
                        updated_at
                    FROM request_schedule_entries
                    WHERE req_id = $1
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                    """,
                    req_id,
                )
                return dict(row) if row else None
