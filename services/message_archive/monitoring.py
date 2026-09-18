from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Protocol

from services.message_archive.ports import MessageArchiveRepositoryPort

log = logging.getLogger("message_archive.monitor")


@dataclass(slots=True)
class ArchiveRuntimeMetrics:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failures: int = 0
    inbound_failures: int = 0
    outbound_failures: int = 0
    import_failures: int = 0

    def record_saved(self, action: str) -> None:
        if action == "created":
            self.created += 1
        elif action == "updated":
            self.updated += 1
        else:
            self.skipped += 1

    def record_failure(self, direction: str) -> None:
        self.failures += 1
        if direction == "inbound":
            self.inbound_failures += 1
        elif direction == "outbound":
            self.outbound_failures += 1
        else:
            self.import_failures += 1


class MediaQueueStatusPort(Protocol):
    @property
    def queue_size(self) -> int: ...

    @property
    def active_jobs(self) -> int: ...


class MessageArchiveMonitor:
    def __init__(
        self,
        *,
        repo: MessageArchiveRepositoryPort,
        runtime: ArchiveRuntimeMetrics,
        media_queue: MediaQueueStatusPort,
        interval_seconds: float = 300.0,
    ) -> None:
        self.repo = repo
        self.runtime = runtime
        self.media_queue = media_queue
        self.interval_seconds = max(5.0, interval_seconds)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task:
            return
        await self._safe_log_snapshot()
        self._task = asyncio.create_task(self._run(), name="message_archive_monitor")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            await self._safe_log_snapshot()

    async def _safe_log_snapshot(self) -> None:
        try:
            await self.log_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Failed to collect message archive health snapshot")

    async def log_snapshot(self) -> dict[str, int]:
        database = await self.repo.get_archive_statistics()
        snapshot = {
            **database,
            "queue_size": self.media_queue.queue_size,
            "active_media_jobs": self.media_queue.active_jobs,
            "runtime_created": self.runtime.created,
            "runtime_updated": self.runtime.updated,
            "runtime_skipped": self.runtime.skipped,
            "runtime_failures": self.runtime.failures,
            "runtime_inbound_failures": self.runtime.inbound_failures,
            "runtime_outbound_failures": self.runtime.outbound_failures,
            "runtime_import_failures": self.runtime.import_failures,
        }
        log.info("Message archive health: %s", snapshot)
        return snapshot

