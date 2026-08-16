from __future__ import annotations

from typing import Protocol

from services.broadcast.models import BroadcastDeliveryStatus


class BroadcastDeliveryPort(Protocol):
    async def send(self, *, chat_id: int) -> BroadcastDeliveryStatus: ...

