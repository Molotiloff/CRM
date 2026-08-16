from __future__ import annotations

import asyncio
from weakref import WeakValueDictionary


class ChatLockRegistry:
    """Process-local coordination; PostgreSQL remains the source of operation state."""

    def __init__(self) -> None:
        self._locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

    def for_chat(self, chat_id: int) -> asyncio.Lock:
        key = int(chat_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def active_count(self) -> int:
        return len(self._locks)
