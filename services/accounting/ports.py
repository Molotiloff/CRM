from __future__ import annotations

from typing import Protocol

from domain import CurrencyCode, FirmPosition, FirmPositionMove, NewFirmPositionMove

from .models import CashChatBinding, CashChatRegistrySyncResult


class CashChatRegistryRepositoryPort(Protocol):
    async def sync_configured(
        self,
        bindings: tuple[CashChatBinding, ...],
    ) -> CashChatRegistrySyncResult: ...


class FirmPositionRepositoryPort(Protocol):
    async def acquire_currency_lock(self, currency: CurrencyCode) -> None: ...

    async def get_current(self, currency: CurrencyCode) -> FirmPosition: ...

    async def list_current(self) -> list[FirmPosition]: ...

    async def append(self, move: NewFirmPositionMove) -> FirmPositionMove: ...

    async def get_by_idempotency_key(self, key: str) -> FirmPositionMove | None: ...

    async def get_move(self, move_id: int) -> FirmPositionMove | None: ...
