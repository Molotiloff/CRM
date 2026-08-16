from __future__ import annotations

from collections import OrderedDict

SessionKey = tuple[str, tuple[int, int]]


class RequestTableSessionStore:
    """Bounded UI cache and same-process concurrency guard, never source of truth."""

    def __init__(self, *, max_marked: int = 5_000) -> None:
        if max_marked < 1:
            raise ValueError("max_marked must be positive")
        self._pending: set[SessionKey] = set()
        self._marked: OrderedDict[SessionKey, None] = OrderedDict()
        self._max_marked = max_marked

    def is_pending(self, scope: str, key: tuple[int, int]) -> bool:
        return (scope, key) in self._pending

    def add_pending(self, scope: str, key: tuple[int, int]) -> None:
        self._pending.add((scope, key))

    def discard_pending(self, scope: str, key: tuple[int, int]) -> None:
        self._pending.discard((scope, key))

    def is_marked(self, scope: str, key: tuple[int, int]) -> bool:
        session_key = (scope, key)
        marked = session_key in self._marked
        if marked:
            self._marked.move_to_end(session_key)
        return marked

    def mark(self, scope: str, key: tuple[int, int]) -> None:
        session_key = (scope, key)
        self._marked.pop(session_key, None)
        self._marked[session_key] = None
        if len(self._marked) > self._max_marked:
            self._marked.popitem(last=False)
