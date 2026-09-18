from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

_START_COMMAND_RE = re.compile(r"(?iu)^/start(?:@\w+)?(?:\s|$)")
_WALLET_CHANGE_RE = re.compile(
    r"(?iu)^/[A-Za-zА-Яа-яЁё0-9_]+(?:@\w+)?\s+"
)
_PARTNER_USDT_RE = re.compile(
    r"(?iu)^/(?:отпр|отпр1|баотпр)(?:@\w+)?(?:\s|$)"
)

SilentMessageHandler = Callable[[Message], Awaitable[Any]]


class SilentAccountingChatsMiddleware(BaseMiddleware):
    """Allow silent wallet bookkeeping and suppress every other chat interaction."""

    def __init__(
        self,
        chat_ids: Iterable[int],
        *,
        start_handler: SilentMessageHandler,
        wallet_change_handler: SilentMessageHandler,
    ) -> None:
        super().__init__()
        self._chat_ids = {int(chat_id) for chat_id in chat_ids}
        self._start_handler = start_handler
        self._wallet_change_handler = wallet_change_handler

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        chat_id = self._chat_id(event)
        if chat_id not in self._chat_ids:
            return await handler(event, data)
        if not isinstance(event, Message):
            return None

        text = (event.text or event.caption or "").strip()
        if _START_COMMAND_RE.match(text):
            return await self._start_handler(event)
        if _WALLET_CHANGE_RE.match(text) or _PARTNER_USDT_RE.match(text):
            return await self._wallet_change_handler(event)
        return None

    @staticmethod
    def _chat_id(event: Any) -> int | None:
        if isinstance(event, Message):
            return int(event.chat.id)
        if isinstance(event, CallbackQuery) and event.message is not None:
            return int(event.message.chat.id)
        return None
