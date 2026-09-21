from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from domain import DealEventPayload


@dataclass(frozen=True, slots=True)
class DealEvent:
    type: str
    deal_id: int
    payload: DealEventPayload

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "deal_id": self.deal_id,
            "payload": self.payload.to_dict(),
        }


class DealEventSubscriberLimitError(RuntimeError):
    pass


class DealEventBus:
    """Best-effort live updates; durable deal state and delivery live in PostgreSQL."""

    def __init__(
        self,
        *,
        queue_size: int = 100,
        max_subscribers: int = 100,
    ) -> None:
        self._queue_size = queue_size
        self._max_subscribers = max_subscribers
        self._subscribers: set[asyncio.Queue[DealEvent]] = set()

    async def publish(self, event: DealEvent) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[DealEvent]]:
        if len(self._subscribers) >= self._max_subscribers:
            raise DealEventSubscriberLimitError("Too many realtime subscribers")
        queue: asyncio.Queue[DealEvent] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
