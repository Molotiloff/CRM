from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from aiogram.types import Chat, Message

from middlewares.message_archive import (
    IncomingMessageArchiveMiddleware,
    OutgoingMessageArchiveMiddleware,
)


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
