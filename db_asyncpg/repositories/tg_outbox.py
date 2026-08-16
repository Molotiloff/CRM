from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo


@dataclass(frozen=True, slots=True)
class TgOutboxItem:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int
    created_at: datetime


class TgOutboxRepository(ConnectionBoundRepo):
    async def count_pending(self, *, max_attempts: int = 5) -> int:
        async with self._connection() as con:
            value = await con.fetchval(
                """
                SELECT COUNT(*)
                FROM tg_outbox
                WHERE attempts < $1
                  AND status IN ('pending', 'processing')
                """,
                max_attempts,
            )
        return int(value or 0)

    async def claim_batch(
        self,
        *,
        limit: int = 20,
        max_attempts: int = 5,
        lock_timeout_seconds: int = 300,
    ) -> list[TgOutboxItem]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                WITH candidates AS (
                    SELECT id
                    FROM tg_outbox
                    WHERE attempts < $2
                      AND (
                        (status = 'pending' AND available_at <= NOW())
                        OR (status = 'processing'
                            AND locked_at < NOW() - $3 * INTERVAL '1 second')
                      )
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT $1
                )
                UPDATE tg_outbox o
                SET status = 'processing', attempts = attempts + 1,
                    locked_at = NOW(), last_error = NULL
                FROM candidates c
                WHERE o.id = c.id
                RETURNING o.id, o.kind, o.payload::text AS payload,
                          o.attempts, o.created_at
                """,
                limit,
                max_attempts,
                lock_timeout_seconds,
            )
        return [
            TgOutboxItem(
                id=int(row["id"]),
                kind=str(row["kind"]),
                payload=json.loads(row["payload"]),
                attempts=int(row["attempts"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def mark_sent(self, outbox_id: int) -> None:
        async with self._connection() as con:
            await con.execute(
                """
                UPDATE tg_outbox
                SET status = 'sent', sent_at = NOW(), locked_at = NULL,
                    last_error = NULL
                WHERE id = $1 AND status = 'processing'
                """,
                outbox_id,
            )

    async def mark_failed(
        self,
        outbox_id: int,
        *,
        error: str,
        retry: bool,
        retry_delay_seconds: int,
    ) -> None:
        async with self._connection() as con:
            await con.execute(
                """
                UPDATE tg_outbox
                SET status = CASE WHEN $3 THEN 'pending' ELSE 'failed' END,
                    available_at = CASE
                        WHEN $3 THEN NOW() + $4 * INTERVAL '1 second'
                        ELSE available_at
                    END,
                    locked_at = NULL,
                    last_error = $2
                WHERE id = $1 AND status = 'processing'
                """,
                outbox_id,
                error[:2000],
                retry,
                retry_delay_seconds,
            )

    async def get_deal_delivery_context(self, deal_id: int) -> dict[str, Any] | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT d.id, d.deal_no, d.deal_type, d.status, d.source_kind, d.comment,
                       d.exchange_client_req_id, d.body::text AS body,
                       c.chat_id AS client_chat_id,
                       erl.client_chat_id AS exchange_client_chat_id,
                       erl.client_message_id AS exchange_client_message_id,
                       erl.request_chat_id AS exchange_request_chat_id,
                       erl.request_message_id AS exchange_request_message_id,
                       erl.request_text AS exchange_request_text,
                       rse.request_chat_id AS cash_request_chat_id,
                       rse.request_message_id AS cash_request_message_id
                FROM deals d
                LEFT JOIN clients c ON c.id = d.client_id
                LEFT JOIN exchange_request_links erl
                    ON erl.client_req_id = d.exchange_client_req_id
                LEFT JOIN request_schedule_entries rse
                    ON rse.req_id = d.body->>'req_id'
                WHERE d.id = $1
                """,
                deal_id,
            )
        if row is None:
            return None
        result = dict(row)
        result["body"] = json.loads(result["body"] or "{}")
        return result
