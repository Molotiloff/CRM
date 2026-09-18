from __future__ import annotations

from collections import OrderedDict

from aiogram.types import Message


class CityCashMediaStore:
    """Short-lived Telegram album assembly; contains no committed operation state."""

    def __init__(self, *, max_groups: int = 500) -> None:
        if max_groups < 1:
            raise ValueError("max_groups must be positive")
        self._groups: OrderedDict[tuple[int, str], list[Message]] = OrderedDict()
        self._max_groups = max_groups

    def add_message(self, *, chat_id: int, media_group_id: str, message: Message) -> None:
        key = (int(chat_id), str(media_group_id))
        messages = self._groups.setdefault(key, [])
        messages.append(message)
        self._groups.move_to_end(key)
        while len(self._groups) > self._max_groups:
            self._groups.popitem(last=False)

    def group_size(self, *, chat_id: int, media_group_id: str) -> int:
        return len(self._groups.get((int(chat_id), str(media_group_id)), []))

    def pop_group(self, *, chat_id: int, media_group_id: str) -> list[Message]:
        messages = self._groups.pop((int(chat_id), str(media_group_id)), [])
        messages.sort(key=lambda message: int(message.message_id))
        return messages
