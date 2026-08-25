from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from domain import CurrencyCode, FirmPosition, FirmPositionMove, NewFirmPositionMove

from .models import (
    CashChatBinding,
    CashChatRegistrySyncResult,
    FirmWalletAddress,
    MainDashboardSnapshot,
    WalletFactSnapshot,
    WalletFactSource,
)


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


class WalletFactRepositoryPort(Protocol):
    async def acquire_network_lock(self, network: str) -> None: ...

    async def acquire_fact_lock(self, currency: CurrencyCode) -> None: ...

    async def get_active_address(self, network: str) -> FirmWalletAddress | None: ...

    async def insert_address(
        self,
        *,
        network: str,
        address: str,
        active_from: datetime,
        reason: str,
        created_by: int | None,
    ) -> FirmWalletAddress: ...

    async def close_address(self, address_id: int, *, active_to: datetime) -> None: ...

    async def latest_snapshot(
        self,
        currency: CurrencyCode,
        *,
        address_id: int | None = None,
    ) -> WalletFactSnapshot | None: ...

    async def find_snapshot_by_idempotency_key(
        self,
        key: str,
    ) -> WalletFactSnapshot | None: ...

    async def append_snapshot(
        self,
        *,
        currency: CurrencyCode,
        actual_qty: Decimal,
        observed_at: datetime,
        source: WalletFactSource,
        address_id: int | None,
        actor_user_id: int | None,
        comment: str | None,
        idempotency_key: str,
    ) -> WalletFactSnapshot: ...


class AccountingDashboardReadPort(Protocol):
    async def get_snapshot(self, *, business_date: date) -> MainDashboardSnapshot: ...
