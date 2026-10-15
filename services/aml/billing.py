from __future__ import annotations

from decimal import Decimal
from typing import Protocol


class AMLBillingPort(Protocol):
    async def wallet_client(self, chat_id: int) -> int | None: ...

    async def charge(self, *, client_id: int, chat_id: int, idempotency_key: str) -> Decimal: ...
