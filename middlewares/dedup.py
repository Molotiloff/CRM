# middlewares/dedup.py
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message


def _key(event: Any):
    if isinstance(event, Message):
        return event.chat.id, event.message_id
    if isinstance(event, CallbackQuery):
        # у колбэков бывает inline_message_id вместо message
        mid = event.inline_message_id or (event.message and event.message.message_id)
        chat_id = (event.message and event.message.chat.id) or 0
        return chat_id, f"cb:{event.id}:{mid}"
    return None


class DedupMiddleware(BaseMiddleware):
    """
    Отбрасывает повторные апдейты (по chat_id, message_id/inline_message_id).
    Память O(N), старые ключи вытесняются (LRU).
    """

    def __init__(self, maxsize: int = 1000) -> None:
        super().__init__()
        self.seen: OrderedDict[tuple[int,int|str], bool] = OrderedDict()
        self.maxsize = maxsize

    async def __call__(
            self,
            handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
            event: Any,
            data: dict[str, Any]
    ) -> Any:
        key = _key(event)
        if key is not None:
            if key in self.seen:
                # дубль — просто игнорируем
                return
            self.seen[key] = True
            if len(self.seen) > self.maxsize:
                self.seen.popitem(last=False)
        return await handler(event, data)
