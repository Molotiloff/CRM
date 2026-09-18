from __future__ import annotations

import logging
from typing import Protocol

from services.message_archive.models import ArchiveMessage, SaveMessageResult
from services.message_archive.monitoring import ArchiveRuntimeMetrics
from services.message_archive.ports import MessageArchiveRepositoryPort

log = logging.getLogger("message_archive")


class AttachmentSchedulerPort(Protocol):
    async def schedule(self, result: SaveMessageResult, *, source_root: str | None = None) -> None: ...


class MessageArchiveService:
    def __init__(
        self,
        *,
        repo: MessageArchiveRepositoryPort,
        attachment_scheduler: AttachmentSchedulerPort | None = None,
        metrics: ArchiveRuntimeMetrics | None = None,
    ) -> None:
        self.repo = repo
        self.attachment_scheduler = attachment_scheduler
        self.metrics = metrics or ArchiveRuntimeMetrics()

    async def archive(
        self, message: ArchiveMessage, *, source_root: str | None = None
    ) -> SaveMessageResult:
        try:
            result = await self.repo.save_archive_message(message)
        except Exception:
            self.metrics.record_failure(message.direction)
            raise
        self.metrics.record_saved(result.action)
        if self.attachment_scheduler:
            await self.attachment_scheduler.schedule(result, source_root=source_root)
        log.debug(
            "Archived message chat_id=%s message_id=%s action=%s attachments=%s",
            result.chat_id,
            message.telegram_message_id,
            result.action,
            len(result.attachments),
        )
        return result
