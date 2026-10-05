from __future__ import annotations

from db_asyncpg.ports.administration import ClientInviteLinkRepository
from db_asyncpg.ports.administration import ClientInviteTarget as ClientInviteTarget

from .base import ConnectionBoundRepo


class ClientInviteLinkRepo(ConnectionBoundRepo, ClientInviteLinkRepository):
    async def list_missing(
        self, *, after_client_id: int, limit: int
    ) -> list[ClientInviteTarget]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT c.id, c.chat_id
                FROM clients c
                WHERE c.id > $1
                  AND c.is_active
                  AND c.chat_id < 0
                  AND c.telegram_invite_link IS NULL
                  AND COALESCE(c.client_group, '') <> 'internal_wallet'
                  AND NOT EXISTS (
                      SELECT 1 FROM cash_chat_registry cash
                      WHERE cash.client_id = c.id AND cash.is_active
                  )
                ORDER BY c.id
                LIMIT $2
                """,
                after_client_id,
                limit,
            )
        return [
            ClientInviteTarget(client_id=int(row["id"]), chat_id=int(row["chat_id"]))
            for row in rows
        ]

    async def save_if_missing(self, target: ClientInviteTarget, link: str) -> bool:
        async with self._connection() as con:
            saved_id = await con.fetchval(
                """
                UPDATE clients SET telegram_invite_link = $3
                WHERE id = $1 AND chat_id = $2 AND is_active
                  AND telegram_invite_link IS NULL
                RETURNING id
                """,
                target.client_id,
                target.chat_id,
                link,
            )
        return saved_id is not None
