from __future__ import annotations

import logging
from typing import Any

from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

from services.messaging.ports import ReplierPort, SentMessageRef
from telegram_adapters.aiogram_messenger import wrap_telegram_error

log = logging.getLogger(__name__)


class AiogramMessageReplier(ReplierPort):
    def __init__(self, message: Message) -> None:
        self._message = message

    async def reply(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: Any | None = None,
        reply_to_message_id: int | None = None,
    ) -> SentMessageRef | None:
        try:
            sent = await self._message.answer(
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
            )
        except TelegramAPIError as error:
            raise wrap_telegram_error(error) from error
        return _message_ref(sent)

    async def alert(self, text: str, *, modal: bool = True) -> None:
        await self._message.answer(text)


class AiogramCallbackReplier(ReplierPort):
    def __init__(self, callback: CallbackQuery) -> None:
        self._callback = callback

    async def reply(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: Any | None = None,
        reply_to_message_id: int | None = None,
    ) -> SentMessageRef | None:
        message = self._callback.message
        if message is None:
            log.warning("Callback without message: reply %r dropped", text[:50])
            return None
        try:
            sent = await message.answer(
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
            )
        except TelegramAPIError as error:
            raise wrap_telegram_error(error) from error
        return _message_ref(sent)

    async def alert(self, text: str, *, modal: bool = True) -> None:
        try:
            await self._callback.answer(text, show_alert=modal)
        except TelegramAPIError as error:
            log.debug("Callback answer failed: %s", error)


def _message_ref(message: Any) -> SentMessageRef:
    return SentMessageRef(
        chat_id=message.chat.id,
        message_id=message.message_id,
        chat_title=getattr(message.chat, "title", None),
    )

