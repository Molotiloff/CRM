from __future__ import annotations

from datetime import datetime
from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo


class BroadcastAudienceRepo(ConnectionBoundRepo):
    async def list_inactive_clients(
        self,
        *,
        cutoff: datetime,
        staff_user_ids: list[int],
    ) -> list[dict[str, Any]]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT c.id, c.chat_id, c.name, c.client_group
                FROM clients c
                JOIN message_archive_chats archive
                  ON archive.telegram_chat_id = c.chat_id AND archive.is_enabled
                JOIN LATERAL (
                    SELECT m.sent_at
                    FROM message_archive_messages m
                    JOIN message_archive_authors a ON a.id = m.author_id
                    WHERE m.chat_id = archive.id
                      AND m.direction IN ('inbound', 'imported')
                      AND m.message_type IN (
                          'text', 'photo', 'video', 'video_note', 'audio', 'voice',
                          'animation', 'document', 'sticker', 'contact', 'location',
                          'venue', 'poll', 'dice', 'story', 'paid_media', 'checklist'
                      )
                      AND a.telegram_user_id IS NOT NULL AND NOT a.is_bot
                      AND NOT (a.telegram_user_id = ANY($2::bigint[]))
                      AND NOT EXISTS (SELECT 1 FROM managers s WHERE s.user_id = a.telegram_user_id)
                      AND NOT EXISTS (SELECT 1 FROM users s WHERE s.tg_user_id = a.telegram_user_id)
                    ORDER BY m.sent_at DESC
                    LIMIT 1
                ) activity ON activity.sent_at < $1
                WHERE c.is_active AND c.created_at < $1
                ORDER BY c.created_at DESC, c.id DESC
                """,
                cutoff,
                staff_user_ids,
            )
        return [dict(row) for row in rows]
