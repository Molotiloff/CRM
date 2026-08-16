from __future__ import annotations

from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo


class LiveMessagesRepo(ConnectionBoundRepo):
    async def upsert_live_message(
        self,
        *,
        chat_id: int,
        message_key: str,
        message_id: int,
    ) -> None:
        async with self._connection() as con:
            async with con.transaction():
                await con.execute(
                    """
                    INSERT INTO live_messages (
                        chat_id,
                        message_key,
                        message_id,
                        updated_at
                    )
                    VALUES ($1, $2, $3, now())
                    ON CONFLICT (chat_id, message_key) DO UPDATE SET
                        message_id = EXCLUDED.message_id,
                        updated_at = now()
                    """,
                    int(chat_id),
                    message_key.strip(),
                    int(message_id),
                )

    async def get_live_message(
        self,
        *,
        chat_id: int,
        message_key: str,
    ) -> dict[str, Any] | None:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    SELECT chat_id, message_key, message_id, updated_at
                    FROM live_messages
                    WHERE chat_id = $1
                      AND message_key = $2
                    LIMIT 1
                    """,
                    int(chat_id),
                    message_key.strip(),
                )
                return dict(row) if row else None

    async def delete_live_message(
        self,
        *,
        chat_id: int,
        message_key: str,
    ) -> bool:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    DELETE FROM live_messages
                    WHERE chat_id = $1
                      AND message_key = $2
                    """,
                    int(chat_id),
                    message_key.strip(),
                )
                return res.endswith(" 1")

    async def list_live_messages(
        self,
        *,
        message_key: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._connection() as con:
            async with con.transaction():

                if message_key:
                    rows = await con.fetch(
                        """
                        SELECT chat_id, message_key, message_id, updated_at
                        FROM live_messages
                        WHERE message_key = $1
                        ORDER BY updated_at DESC, chat_id ASC
                        """,
                        message_key.strip(),
                    )
                else:
                    rows = await con.fetch(
                        """
                        SELECT chat_id, message_key, message_id, updated_at
                        FROM live_messages
                        ORDER BY updated_at DESC, chat_id ASC
                        """
                    )

                return [dict(r) for r in rows]
