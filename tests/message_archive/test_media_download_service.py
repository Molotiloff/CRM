from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from services.message_archive.media_download_service import MediaDownloadService
from services.message_archive.media_storage import StoredMedia
from services.message_archive.models import SavedAttachment, SaveMessageResult


class _Repo:
    def __init__(self) -> None:
        self.ready: list[tuple[int, str]] = []

    async def list_pending_archive_attachments(self, *, limit=500):
        return []

    async def mark_archive_attachment_ready(
        self, *, attachment_id, storage_key, sha256, file_size
    ):
        self.ready.append((attachment_id, storage_key))

    async def mark_archive_attachment_failed(
        self, *, attachment_id, error, unavailable=False
    ):
        raise AssertionError(error)


class _Storage:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    async def put(self, *, key, chunks):
        value = b"".join([chunk async for chunk in chunks])
        self.values[key] = value
        return StoredMedia(key=key, sha256="hash", size=len(value))


def _result(attachment_id: int, source_path: str) -> SaveMessageResult:
    return SaveMessageResult(
        message_id=123,
        chat_id=7,
        sent_at=datetime(2026, 9, 13, tzinfo=UTC),
        action="created",
        attachments=(
            SavedAttachment(
                id=attachment_id,
                attachment_type="photo",
                ordinal=0,
                telegram_file_id=None,
                source_path=source_path,
                download_status="pending",
            ),
        ),
    )


def test_import_media_uses_backpressure_and_calendar_storage_key(tmp_path) -> None:
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "one.jpg").write_bytes(b"one")
    (tmp_path / "photos" / "two.jpg").write_bytes(b"two")
    repo = _Repo()
    storage = _Storage()
    service = MediaDownloadService(
        repo=repo,
        storage=storage,
        workers=1,
        queue_size=1,
        recovery_interval_seconds=3600,
    )

    async def scenario() -> None:
        await service.start()
        try:
            await service.schedule(_result(1, "photos/one.jpg"), source_root=str(tmp_path))
            await service.schedule(_result(2, "photos/two.jpg"), source_root=str(tmp_path))
            await service.wait_until_idle()
        finally:
            await service.stop()

    asyncio.run(scenario())

    assert repo.ready == [
        (1, "7/2026/09/123/1.jpg"),
        (2, "7/2026/09/123/2.jpg"),
    ]
    assert list(storage.values.values()) == [b"one", b"two"]
