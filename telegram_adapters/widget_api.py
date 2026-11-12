"""Bot API 10.4 compatibility until the pinned aiogram exposes widgets.

Use Bot.__call__: shared session, proxy, test API URL and archive middleware apply.
No monkey patches and no second Telegram HTTP client.
"""

from typing import Any, ClassVar

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.methods import TelegramMethod
from aiogram.types import Message

from services.rate_order.widget_errors import WidgetDeliveryError


class SendWidget(TelegramMethod[Message]):
    __api_method__: ClassVar[str] = "sendWidget"
    __returning__ = Message
    chat_id: int
    html: str
    chat_data: str
    caption: str
    height: int = 360
    allow_queries: bool = True
    parse_mode: str | None = None


class EditMessageWidgetData(TelegramMethod[bool]):
    __api_method__: ClassVar[str] = "editMessageWidgetData"
    __returning__ = bool
    chat_id: int
    message_id: int
    chat_data: str


class AnswerWidgetQuery(TelegramMethod[bool]):
    __api_method__: ClassVar[str] = "answerWidgetQuery"
    __returning__ = bool
    widget_query_id: str
    data: str | None = None


class WidgetApi:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def _call(self, method: TelegramMethod[Any]) -> Any:
        try:
            return await self.bot(method)
        except TelegramRetryAfter as exc:
            raise WidgetDeliveryError("retry", retry_after=exc.retry_after) from exc
        except TelegramForbiddenError as exc:
            raise WidgetDeliveryError("forbidden") from exc
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return None
            kind = "missing" if "message to edit not found" in str(exc).lower() else "bad_request"
            raise WidgetDeliveryError(kind) from exc
        except TelegramAPIError as exc:
            raise WidgetDeliveryError("unavailable") from exc

    async def send(self, *, chat_id: int, html: str, state: str, caption: str) -> int:
        message = await self._call(
            SendWidget(chat_id=chat_id, html=html, chat_data=state, caption=caption)
        )
        return message.message_id

    async def edit(self, *, chat_id: int, message_id: int, state: str) -> None:
        await self._call(
            EditMessageWidgetData(chat_id=chat_id, message_id=message_id, chat_data=state)
        )

    async def caption(self, *, chat_id: int, message_id: int, text: str) -> Any:
        from aiogram.methods import EditMessageCaption

        return await self._call(
            EditMessageCaption(
                chat_id=chat_id, message_id=message_id, caption=text, parse_mode=None
            )
        )

    async def answer(self, query_id: str, data: str | None = None) -> None:
        await self._call(AnswerWidgetQuery(widget_query_id=query_id, data=data))

    async def text(self, *, chat_id: int, message_id: int | None, text: str) -> int:
        from aiogram.methods import EditMessageText, SendMessage

        if message_id is not None:
            await self._call(
                EditMessageText(chat_id=chat_id, message_id=message_id, text=text, parse_mode=None)
            )
            return message_id
        sent = await self._call(SendMessage(chat_id=chat_id, text=text, parse_mode=None))
        return sent.message_id
