from __future__ import annotations

import asyncio
import zipfile
from datetime import UTC, datetime, timedelta

from services.message_archive.export_service import MessageExportService
from services.message_archive.media_storage import LocalMediaStorage


class _FakeRepo:
    def __init__(self, messages: list[dict], attachments: list[dict]) -> None:
        self.messages = messages
        self.attachments = attachments

    async def get_archive_chat(self, chat_id: int):
        return {"id": chat_id, "current_title": "Тест / чат", "telegram_chat_id": -1001}

    async def list_archive_messages_page(
        self, *, chat_id, cursor_sent_at=None, cursor_message_id=None, limit=500
    ):
        if cursor_sent_at is None:
            return self.messages[:limit]
        return [
            row
            for row in self.messages
            if (row["sent_at"], row["telegram_message_id"])
            > (cursor_sent_at, cursor_message_id)
        ][:limit]

    async def list_archive_attachments_for_messages(self, message_ids):
        return [row for row in self.attachments if row["message_id"] in message_ids]


async def _bytes(value: bytes):
    yield value


def test_export_builds_valid_split_zip_with_safe_html(tmp_path) -> None:
    storage = LocalMediaStorage(tmp_path / "media")
    asyncio.run(storage.put(key="1/photo.jpg", chunks=_bytes(b"jpeg-data")))
    base = datetime(2026, 9, 10, tzinfo=UTC)
    messages = [
        {
            "id": index,
            "telegram_message_id": index,
            "sent_at": base + timedelta(minutes=index),
            "message_type": "message",
            "direction": "inbound",
            "text_plain": "<unsafe>" + "x" * 700_000,
            "text_entities": [],
            "source": "telegram_desktop",
            "edited_at": None,
            "author_name": "Tester",
        }
        for index in range(1, 4)
    ]
    attachments = [
        {
            "id": 10,
            "message_id": 1,
            "attachment_type": "photo",
            "ordinal": 0,
            "download_status": "ready",
            "storage_key": "1/photo.jpg",
            "file_size": 9,
        }
    ]
    service = MessageExportService(
        repo=_FakeRepo(messages, attachments),
        storage=storage,
        temp_dir=tmp_path / "tmp",
        part_size=1024 * 1024,
        page_size=10,
    )

    generated = asyncio.run(service.generate(chat_id=1))
    try:
        assert len(generated.parts) == 3
        assert sum(part.message_count for part in generated.parts) == 3
        with zipfile.ZipFile(generated.parts[0].path) as archive:
            assert set(archive.namelist()) == {"index.html", "manifest.json", "media/photo_1_0.jpg"}
            html = archive.read("index.html").decode()
            assert "<unsafe>" not in html
            assert "&lt;unsafe&gt;" in html
    finally:
        generated.cleanup()

