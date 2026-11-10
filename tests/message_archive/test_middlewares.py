from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import SendMediaGroup
from aiogram.types import BufferedInputFile, Chat, InputMediaPhoto, Message, PhotoSize

from middlewares.message_archive import (
    IncomingMessageArchiveMiddleware,
    OutgoingMessageArchiveMiddleware,
)
from telegram_adapters.memory_document import EphemeralBufferedInputFile


def _message() -> Message:
    return Message(
        message_id=1,
        date=datetime(2026, 9, 10, tzinfo=UTC),
        chat=Chat(id=10, type="private", first_name="Test"),
        text="hello",
    )


class _ArchiveService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[bool] = []

    async def archive(self, message):
        self.calls.append(message.direction == "outbound")
        if self.fail:
            raise RuntimeError("database unavailable")


def test_incoming_archive_failure_does_not_block_business_handler() -> None:
    service = _ArchiveService(fail=True)
    middleware = IncomingMessageArchiveMiddleware(service)
    handled = []

    async def handler(event, data):
        handled.append(event.message_id)
        return "ok"

    result = asyncio.run(middleware(handler, _message(), {}))

    assert result == "ok"
    assert handled == [1]
    assert service.calls == [False]


def test_outgoing_middleware_archives_message_after_successful_request() -> None:
    service = _ArchiveService()
    middleware = OutgoingMessageArchiveMiddleware(service)
    message = _message()

    async def make_request(bot, method):
        return message

    result = asyncio.run(middleware(make_request, None, object()))

    assert result is message
    assert service.calls == [True]


@pytest.mark.parametrize("generated", [False, True])
async def test_album_pages_without_captions_are_metadata_only_only_for_generated_aml(generated):
    service = AsyncMock()
    middleware = OutgoingMessageArchiveMiddleware(service)
    file_type = EphemeralBufferedInputFile if generated else BufferedInputFile
    files = [file_type(b"png", filename=f"SKYEX_AML_{index}.png") for index in range(2)]
    method = SendMediaGroup(chat_id=10, media=[InputMediaPhoto(media=file, parse_mode=None) for file in files])
    messages = [_message().model_copy(update={
        "message_id": index + 1, "text": None, "caption": None, "media_group_id": "album",
        "photo": [PhotoSize(file_id=f"photo-{index}", file_unique_id=f"unique-{index}", width=1200, height=1600)],
    }) for index in range(2)]
    try:
        result = await middleware(AsyncMock(return_value=messages), None, method)
        assert result is messages
        assert service.archive.await_count == 2
        for index, call in enumerate(service.archive.call_args_list):
            attachment = call.args[0].attachments[0]
            assert attachment.telegram_file_id == f"photo-{index}"
            assert attachment.download_status == ("skipped" if generated else "pending")
    finally:
        for file in files:
            if isinstance(file, EphemeralBufferedInputFile):
                file.release()
