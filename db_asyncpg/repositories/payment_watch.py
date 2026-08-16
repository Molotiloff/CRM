from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from db_asyncpg.repositories.base import BaseRepo


class PaymentWatchRepo(BaseRepo):
    async def create_payment_watch(
        self,
        *,
        chat_id: int,
        chat_name: str | None,
        reply_message_id: int,
        address: str,
        our_address: str,
        created_by_user_id: int | None,
        mode: str,
        phase: str,
        status: str,
        timeout_at: datetime,
    ) -> int:
        timeout_norm = self._normalize_dt(timeout_at)
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    INSERT INTO payment_watches (
                        chat_id,
                        chat_name,
                        reply_message_id,
                        address,
                        our_address,
                        created_by_user_id,
                        mode,
                        phase,
                        status,
                        timeout_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    RETURNING id
                    """,
                    int(chat_id),
                    str(chat_name) if chat_name else None,
                    int(reply_message_id),
                    str(address),
                    str(our_address),
                    int(created_by_user_id) if created_by_user_id is not None else None,
                    str(mode).upper(),
                    str(phase).upper(),
                    str(status).upper(),
                    timeout_norm,
                )
                return int(row["id"])

    async def get_payment_watch(self, *, watch_id: int) -> dict | None:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    SELECT *
                    FROM payment_watches
                    WHERE id = $1
                    """,
                    int(watch_id),
                )
                return dict(row) if row else None

    async def get_active_payment_watch_by_reply(
        self,
        *,
        chat_id: int,
        reply_message_id: int,
    ) -> dict | None:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    SELECT *
                    FROM payment_watches
                    WHERE chat_id = $1
                      AND reply_message_id = $2
                      AND status IN ('WATCHING', 'TIMED_OUT')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    int(chat_id),
                    int(reply_message_id),
                )
                return dict(row) if row else None

    async def list_watching_payment_watches(self, *, limit: int = 100) -> list[dict]:
        async with self._connection() as con:
            async with con.transaction():
                rows = await con.fetch(
                    """
                    SELECT *
                    FROM payment_watches
                    WHERE status = 'WATCHING'
                    ORDER BY timeout_at ASC, id ASC
                    LIMIT $1
                    """,
                    int(limit),
                )
                return [dict(row) for row in rows]

    async def touch_payment_watch_checked_at(self, *, watch_id: int, checked_at: datetime) -> None:
        checked_norm = self._normalize_dt(checked_at)
        async with self._connection() as con:
            async with con.transaction():
                await con.execute(
                    """
                    UPDATE payment_watches
                    SET last_checked_at = $2
                    WHERE id = $1
                    """,
                    int(watch_id),
                    checked_norm,
                )

    async def add_payment_watch_event(
        self,
        *,
        watch_id: int,
        tx_hash: str,
        event_type: str,
        direction: str,
        amount: Decimal,
        token_symbol: str,
        confirmations: int,
        block_ts: datetime,
    ) -> int:
        block_ts_norm = self._normalize_dt(block_ts)
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    INSERT INTO payment_watch_events (
                        watch_id,
                        tx_hash,
                        event_type,
                        direction,
                        amount,
                        token_symbol,
                        confirmations,
                        block_ts
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (watch_id, tx_hash) DO UPDATE SET
                        confirmations = GREATEST(payment_watch_events.confirmations, EXCLUDED.confirmations)
                    RETURNING id
                    """,
                    int(watch_id),
                    str(tx_hash),
                    str(event_type).upper(),
                    str(direction).upper(),
                    amount,
                    str(token_symbol).upper(),
                    int(confirmations),
                    block_ts_norm,
                )
                return int(row["id"])

    async def list_payment_watch_events(self, *, watch_id: int) -> list[dict]:
        async with self._connection() as con:
            async with con.transaction():
                rows = await con.fetch(
                    """
                    SELECT *
                    FROM payment_watch_events
                    WHERE watch_id = $1
                    ORDER BY block_ts ASC, id ASC
                    """,
                    int(watch_id),
                )
                return [dict(row) for row in rows]

    async def get_payment_watch_event_hashes(self, *, watch_id: int) -> set[str]:
        async with self._connection() as con:
            async with con.transaction():
                rows = await con.fetch(
                    """
                    SELECT tx_hash
                    FROM payment_watch_events
                    WHERE watch_id = $1
                    """,
                    int(watch_id),
                )
                return {str(row["tx_hash"]) for row in rows}

    async def set_payment_watch_phase(self, *, watch_id: int, phase: str) -> bool:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE payment_watches
                    SET phase = $2
                    WHERE id = $1
                    """,
                    int(watch_id),
                    str(phase).upper(),
                )
                return res.endswith(" 1")

    async def mark_payment_watch_timed_out(self, *, watch_id: int) -> bool:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE payment_watches
                    SET status = 'TIMED_OUT',
                        timed_out_at = NOW()
                    WHERE id = $1
                      AND status = 'WATCHING'
                    """,
                    int(watch_id),
                )
                return res.endswith(" 1")

    async def continue_payment_watch(self, *, watch_id: int, timeout_at: datetime) -> bool:
        timeout_norm = self._normalize_dt(timeout_at)
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE payment_watches
                    SET status = 'WATCHING',
                        timeout_at = $2,
                        continue_count = continue_count + 1
                    WHERE id = $1
                      AND status = 'TIMED_OUT'
                    """,
                    int(watch_id),
                    timeout_norm,
                )
                return res.endswith(" 1")

    async def stop_payment_watch(self, *, watch_id: int) -> bool:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE payment_watches
                    SET status = 'STOPPED',
                        stopped_at = NOW()
                    WHERE id = $1
                      AND status IN ('WATCHING', 'TIMED_OUT')
                    """,
                    int(watch_id),
                )
                return res.endswith(" 1")

    async def complete_payment_watch(self, *, watch_id: int) -> bool:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE payment_watches
                    SET status = 'COMPLETED',
                        completed_at = NOW()
                    WHERE id = $1
                      AND status <> 'COMPLETED'
                    """,
                    int(watch_id),
                )
                return res.endswith(" 1")

    async def set_payment_watch_notice_message_id(
        self,
        *,
        watch_id: int,
        notice_message_id: int,
    ) -> bool:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE payment_watches
                    SET notice_message_id = $2
                    WHERE id = $1
                    """,
                    int(watch_id),
                    int(notice_message_id),
                )
                return res.endswith(" 1")
