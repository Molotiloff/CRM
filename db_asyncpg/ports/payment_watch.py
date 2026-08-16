from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol


class PaymentWatchRepositoryPort(Protocol):
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
    ) -> int: ...

    async def get_payment_watch(self, *, watch_id: int) -> dict | None: ...

    async def get_active_payment_watch_by_reply(
        self,
        *,
        chat_id: int,
        reply_message_id: int,
    ) -> dict | None: ...

    async def list_watching_payment_watches(self, *, limit: int = 100) -> list[dict]: ...

    async def touch_payment_watch_checked_at(
        self,
        *,
        watch_id: int,
        checked_at: datetime,
    ) -> None: ...

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
    ) -> int: ...

    async def list_payment_watch_events(self, *, watch_id: int) -> list[dict]: ...

    async def get_payment_watch_event_hashes(self, *, watch_id: int) -> set[str]: ...

    async def set_payment_watch_phase(self, *, watch_id: int, phase: str) -> bool: ...

    async def mark_payment_watch_timed_out(self, *, watch_id: int) -> bool: ...

    async def continue_payment_watch(self, *, watch_id: int, timeout_at: datetime) -> bool: ...

    async def stop_payment_watch(self, *, watch_id: int) -> bool: ...

    async def complete_payment_watch(self, *, watch_id: int) -> bool: ...

    async def set_payment_watch_notice_message_id(
        self,
        *,
        watch_id: int,
        notice_message_id: int,
    ) -> bool: ...
