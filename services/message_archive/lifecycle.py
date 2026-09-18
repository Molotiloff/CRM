from __future__ import annotations

from typing import Protocol

from services.message_archive.media_download_service import MediaDownloadService
from services.message_archive.monitoring import MessageArchiveMonitor


class ExportTaskController(Protocol):
    async def stop(self) -> None: ...


class MessageArchiveLifecycle:
    """Coordinates archive workers and pending export tasks as one runtime unit."""

    def __init__(
        self,
        *,
        media: MediaDownloadService,
        monitor: MessageArchiveMonitor,
        exports: ExportTaskController,
    ) -> None:
        self._media = media
        self._monitor = monitor
        self._exports = exports
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        await self._media.start()
        try:
            await self._monitor.start()
        except BaseException:
            await self._media.stop()
            raise
        self._running = True

    async def stop(self) -> None:
        try:
            await self._exports.stop()
        finally:
            try:
                await self._monitor.stop()
            finally:
                await self._media.stop()
                self._running = False
