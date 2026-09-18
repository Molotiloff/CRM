from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from services.message_archive.media_storage import MediaStoragePort
from services.message_archive.models import SavedAttachment, SaveMessageResult
from services.message_archive.ports import MessageArchiveRepositoryPort

log = logging.getLogger("message_archive.media")


class TelegramMediaSourcePort(Protocol):
    def stream(self, file_id: str) -> AsyncIterator[bytes]: ...


@dataclass(frozen=True, slots=True)
class _DownloadJob:
    attachment: SavedAttachment
    chat_id: int
    message_id: int
    sent_at: datetime
    source_root: str | None = None


class MediaDownloadService:
    def __init__(
        self,
        *,
        repo: MessageArchiveRepositoryPort,
        storage: MediaStoragePort,
        telegram_media: TelegramMediaSourcePort | None = None,
        workers: int = 2,
        queue_size: int = 500,
        recovery_interval_seconds: float = 30.0,
    ) -> None:
        self.repo = repo
        self.storage = storage
        self.telegram_media = telegram_media
        self.worker_count = max(1, workers)
        self.recovery_interval_seconds = max(1.0, recovery_interval_seconds)
        self._queue: asyncio.Queue[_DownloadJob] = asyncio.Queue(maxsize=max(1, queue_size))
        self._workers: list[asyncio.Task[None]] = []
        self._recovery_task: asyncio.Task[None] | None = None
        self._scheduled_ids: set[int] = set()
        self._stopped = False

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def active_jobs(self) -> int:
        return len(self._scheduled_ids)

    async def start(self) -> None:
        if self._workers:
            return
        self._stopped = False
        self._workers = [
            asyncio.create_task(self._worker(), name=f"message_media_worker:{index}")
            for index in range(self.worker_count)
        ]
        await self._recover_pending_once()
        self._recovery_task = asyncio.create_task(
            self._recovery_loop(), name="message_media_recovery"
        )

    async def _recover_pending_once(self) -> None:
        pending = await self.repo.list_pending_archive_attachments(limit=self._queue.maxsize)
        for row in pending:
            attachment = SavedAttachment(
                id=int(row["id"]),
                attachment_type=str(row["attachment_type"]),
                ordinal=int(row["ordinal"]),
                telegram_file_id=row.get("telegram_file_id"),
                source_path=None,
                download_status=str(row["download_status"]),
            )
            if attachment.telegram_file_id:
                self._schedule_nowait(
                    _DownloadJob(
                        attachment=attachment,
                        chat_id=int(row["chat_id"]),
                        message_id=int(row["telegram_message_id"]),
                        sent_at=row["sent_at"],
                    )
                )

    async def _recovery_loop(self) -> None:
        while not self._stopped:
            await asyncio.sleep(self.recovery_interval_seconds)
            try:
                await self._recover_pending_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Failed to recover pending message archive media")

    async def stop(self) -> None:
        self._stopped = True
        if self._recovery_task:
            self._recovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recovery_task
            self._recovery_task = None
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._workers.clear()
        self._scheduled_ids.clear()

    async def wait_until_idle(self) -> None:
        await self._queue.join()

    async def schedule(self, result: SaveMessageResult, *, source_root: str | None = None) -> None:
        for attachment in result.attachments:
            if attachment.download_status == "ready":
                continue
            if attachment.attachment_type not in {"photo", "voice"}:
                continue
            if not attachment.telegram_file_id and not attachment.source_path:
                continue
            job = _DownloadJob(
                attachment=attachment,
                chat_id=result.chat_id,
                message_id=result.message_id,
                sent_at=result.sent_at,
                source_root=source_root,
            )
            if source_root:
                await self._schedule_wait(job)
            else:
                self._schedule_nowait(job)

    def _schedule_nowait(self, job: _DownloadJob) -> bool:
        if job.attachment.id in self._scheduled_ids:
            return True
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            log.warning("Media queue is full; attachment remains pending id=%s", job.attachment.id)
            return False
        self._scheduled_ids.add(job.attachment.id)
        return True

    async def _schedule_wait(self, job: _DownloadJob) -> None:
        if job.attachment.id in self._scheduled_ids:
            return
        self._scheduled_ids.add(job.attachment.id)
        try:
            await self._queue.put(job)
        except BaseException:
            self._scheduled_ids.discard(job.attachment.id)
            raise

    async def _worker(self) -> None:
        while not self._stopped:
            job = await self._queue.get()
            try:
                await self._download_with_retries(job)
            finally:
                self._scheduled_ids.discard(job.attachment.id)
                self._queue.task_done()

    async def _download_with_retries(self, job: _DownloadJob) -> None:
        for attempt in range(5):
            try:
                await self._download(job)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — boundary of a resilient background worker
                unavailable = isinstance(exc, FileNotFoundError | ValueError)
                await self.repo.mark_archive_attachment_failed(
                    attachment_id=job.attachment.id,
                    error=f"{type(exc).__name__}: {exc}",
                    unavailable=unavailable,
                )
                log.warning(
                    "Media download failed attachment_id=%s attempt=%s: %s",
                    job.attachment.id,
                    attempt + 1,
                    exc,
                )
                if unavailable or attempt >= 4 or self._stopped:
                    return
                await asyncio.sleep(min(2**attempt, 30))

    async def _download(self, job: _DownloadJob) -> None:
        extension = self._extension(job.attachment)
        key = (
            f"{job.chat_id}/{job.sent_at:%Y}/{job.sent_at:%m}/"
            f"{job.message_id}/{job.attachment.id}{extension}"
        )
        if job.attachment.source_path:
            if not job.source_root:
                raise ValueError("Desktop media source root is unavailable")
            chunks = self._local_chunks(job.source_root, job.attachment.source_path)
        elif job.attachment.telegram_file_id:
            chunks = self._telegram_chunks(job.attachment.telegram_file_id)
        else:
            raise ValueError("Attachment has no downloadable source")

        stored = await self.storage.put(key=key, chunks=chunks)
        await self.repo.mark_archive_attachment_ready(
            attachment_id=job.attachment.id,
            storage_key=stored.key,
            sha256=stored.sha256,
            file_size=stored.size,
        )

    async def _local_chunks(self, root: str, relative_path: str) -> AsyncIterator[bytes]:
        root_path = Path(root).expanduser().resolve()
        source = (root_path / relative_path).resolve()
        if not source.is_relative_to(root_path):
            raise ValueError("Desktop export media path escapes export root")
        if not source.is_file():
            raise FileNotFoundError(source)
        with source.open("rb") as input_file:
            while chunk := await asyncio.to_thread(input_file.read, 64 * 1024):
                yield chunk

    async def _telegram_chunks(self, file_id: str) -> AsyncIterator[bytes]:
        if self.telegram_media is None:
            raise RuntimeError("Telegram media source is not configured")
        async for chunk in self.telegram_media.stream(file_id):
            yield chunk

    @staticmethod
    def _extension(attachment: SavedAttachment) -> str:
        if attachment.source_path:
            suffix = Path(attachment.source_path).suffix.lower()
            if suffix and len(suffix) <= 10 and suffix[1:].isalnum():
                return suffix
        default_mime = "image/jpeg" if attachment.attachment_type == "photo" else "audio/ogg"
        return mimetypes.guess_extension(default_mime) or ".bin"
