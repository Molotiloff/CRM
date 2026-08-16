"""Aiogram implementation of the transport-neutral messaging port."""

from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile

from services.messaging.ports import MessengerError, MessengerPort, SentMessageRef
from telegram_adapters.errors import SUPPRESSIBLE_TELEGRAM_ERRORS


def wrap_telegram_error(error: TelegramAPIError) -> MessengerError:
    return MessengerError(
        str(error),
        benign=isinstance(error, SUPPRESSIBLE_TELEGRAM_ERRORS),
    )


class AiogramMessenger(MessengerPort):
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
        reply_to_message_id: int | None = None,
        disable_notification: bool = False,
    ) -> SentMessageRef:
        try:
            sent = await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
                disable_notification=disable_notification,
            )
        except TelegramAPIError as error:
            raise self._wrap(error) from error
        return self._message_ref(sent)

    async def send_photo(
        self,
        chat_id: int,
        photo_bytes: bytes,
        *,
        filename: str,
        caption: str | None = None,
        reply_markup: Any | None = None,
        reply_to_message_id: int | None = None,
        disable_notification: bool = False,
    ) -> SentMessageRef:
        try:
            sent = await self._bot.send_photo(
                chat_id=chat_id,
                photo=BufferedInputFile(photo_bytes, filename=filename),
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
                disable_notification=disable_notification,
            )
        except TelegramAPIError as error:
            raise self._wrap(error) from error
        return self._message_ref(sent)

    async def edit_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
        preserve_reply_markup: bool = False,
    ) -> None:
        kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if not preserve_reply_markup:
            kwargs["reply_markup"] = reply_markup
        try:
            await self._bot.edit_message_text(**kwargs)
        except TelegramAPIError as error:
            raise self._wrap(error) from error

    async def edit_caption(
        self,
        chat_id: int,
        message_id: int,
        caption: str,
        *,
        reply_markup: Any | None = None,
        preserve_reply_markup: bool = False,
    ) -> None:
        kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": caption,
            "parse_mode": "HTML",
        }
        if not preserve_reply_markup:
            kwargs["reply_markup"] = reply_markup
        try:
            await self._bot.edit_message_caption(**kwargs)
        except TelegramAPIError as error:
            raise self._wrap(error) from error

    async def edit_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: Any | None = None,
    ) -> None:
        try:
            await self._bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
        except TelegramAPIError as error:
            raise self._wrap(error) from error

    async def delete(self, chat_id: int, message_id: int) -> None:
        try:
            await self._bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramAPIError as error:
            raise self._wrap(error) from error

    @staticmethod
    def _message_ref(message: Any) -> SentMessageRef:
        return SentMessageRef(
            chat_id=message.chat.id,
            message_id=message.message_id,
            chat_title=getattr(message.chat, "title", None),
        )

    @staticmethod
    def _wrap(error: TelegramAPIError) -> MessengerError:
        return wrap_telegram_error(error)

