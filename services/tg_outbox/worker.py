from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from db_asyncpg.repositories.tg_outbox import TgOutboxItem
from observability import (
    NULL_METRICS,
    MetricsRecorder,
    measured_operation,
    operation_context,
)
from services.lifecycle import ManagedTaskLifecycle

from .service import DealTelegramSyncService

log = logging.getLogger(__name__)


class TgOutboxRepositoryPort(Protocol):
    async def claim_batch(
        self,
        *,
        limit: int,
        max_attempts: int,
        lock_timeout_seconds: int,
    ) -> list[TgOutboxItem]: ...

    async def mark_sent(self, outbox_id: int) -> None: ...

    async def mark_failed(
        self,
        outbox_id: int,
        *,
        error: str,
        retry: bool,
        retry_delay_seconds: int,
    ) -> None: ...

    async def count_pending(self, *, max_attempts: int) -> int: ...


class TgOutboxWorker(ManagedTaskLifecycle):
    def __init__(
        self,
        *,
        repository: TgOutboxRepositoryPort,
        delivery_service: DealTelegramSyncService,
        interval_seconds: int = 2,
        batch_size: int = 20,
        max_attempts: int = 5,
        lock_timeout_seconds: int = 300,
        metrics: MetricsRecorder = NULL_METRICS,
    ) -> None:
        super().__init__(task_name="tg_outbox_worker")
        self._repository = repository
        self._delivery_service = delivery_service
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._lock_timeout_seconds = lock_timeout_seconds
        self._metrics = metrics
        self._metrics.set_queue_size("tg_outbox.pending", 0)

    @measured_operation("tg_outbox.process_batch")
    async def process_once(self) -> int:
        items = await self._repository.claim_batch(
            limit=self._batch_size,
            max_attempts=self._max_attempts,
            lock_timeout_seconds=self._lock_timeout_seconds,
        )
        for item in items:
            with operation_context(
                "tg_outbox.deliver",
                deal_id=item.payload.get("dealId"),
            ):
                try:
                    await self._delivery_service.deliver(item)
                except Exception as exc:
                    retry = item.attempts < self._max_attempts
                    delay = min(300, 2 ** min(item.attempts, 8))
                    await self._repository.mark_failed(
                        item.id,
                        error=f"{type(exc).__name__}: {exc}",
                        retry=retry,
                        retry_delay_seconds=delay,
                    )
                    log.exception(
                        "Telegram outbox delivery failed",
                        extra={
                            "event": "tg_outbox.failed",
                            "outbox_id": item.id,
                            "attempt": item.attempts,
                            "retry": retry,
                        },
                    )
                else:
                    await self._repository.mark_sent(item.id)
                    log.info(
                        "Telegram outbox delivery completed",
                        extra={
                            "event": "tg_outbox.sent",
                            "outbox_id": item.id,
                            "attempt": item.attempts,
                        },
                    )
        pending = await self._repository.count_pending(max_attempts=self._max_attempts)
        self._metrics.set_queue_size("tg_outbox.pending", pending)
        return len(items)

    async def _run(self) -> None:
        while not self.is_stopping:
            try:
                processed = await self.process_once()
            except Exception:
                log.exception("Telegram outbox poll iteration failed")
                processed = 0
            if not processed:
                await asyncio.sleep(self._interval_seconds)
