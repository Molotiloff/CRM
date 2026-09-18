from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.client.session.middlewares.base import NextRequestMiddlewareType
from aiogram.methods import TelegramMethod
from aiogram.types import Message

from services.message_archive.archive_service import MessageArchiveService
from telegram_adapters.message_archive import AiogramMessageArchiveNormalizer

log = logging.getLogger("message_archive.middleware")


class IncomingMessageArchiveMiddleware(BaseMiddleware):
    def __init__(self, service: MessageArchiveService) -> None:
        self.service = service

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            try:
                await self.service.archive(
                    AiogramMessageArchiveNormalizer.normalize(event, outbound=False)
                )
            except Exception:
                log.exception(
                    "Failed to archive incoming message chat_id=%s message_id=%s",
                    event.chat.id,
                    event.message_id,
                )
        return await handler(event, data)


class OutgoingMessageArchiveMiddleware:
    def __init__(self, service: MessageArchiveService) -> None:
        self.service = service

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[Any],
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        result = await make_request(bot, method)
        messages: list[Message] = []
        if isinstance(result, Message):
            messages.append(result)
        elif isinstance(result, list):
            messages.extend(item for item in result if isinstance(item, Message))
        for message in messages:
            try:
                await self.service.archive(
                    AiogramMessageArchiveNormalizer.normalize(message, outbound=True)
                )
            except Exception:
                log.exception(
                    "Failed to archive outgoing message method=%s chat_id=%s message_id=%s",
                    type(method).__name__,
                    message.chat.id,
                    message.message_id,
                )
        return result
