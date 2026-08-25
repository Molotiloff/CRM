from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from domain import CurrencyCode, FirmPosition, FirmPositionMove, NewFirmPositionMove

from .cash_settlement_models import (
    CashSettlementCommand,
    CashSettlementContext,
    CashSettlementResult,
)
from .fulfillment_models import (
    ClientWithdrawalContext,
    EnqueueFulfillment,
    FulfillmentQueueItem,
    FulfillmentQueueSummary,
    ReorderFulfillment,
)
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


class CashSettlementRepositoryPort(Protocol):
    async def acquire_request_lock(self, *, request_id: str) -> None: ...

    async def get_request_for_update(
        self,
        *,
        request_id: str,
    ) -> CashSettlementContext | None: ...

    async def get_context_for_update(
        self,
        *,
        request_id: str,
        city_chat_id: int,
    ) -> CashSettlementContext | None: ...

    async def get_by_request(
        self,
        *,
        request_id: str,
    ) -> CashSettlementResult | None: ...

    async def insert(
        self,
        *,
        context: CashSettlementContext,
        command: CashSettlementCommand,
        actual_qty: Decimal,
        cash_transaction_id: int,
        client_transaction_id: int,
        position_move_id: int | None,
    ) -> CashSettlementResult: ...


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


class FulfillmentQueueRepositoryPort(Protocol):
    async def acquire_client_lock(self, chat_id: int) -> None: ...

    async def client_withdrawal_context(
        self,
        *,
        chat_id: int,
    ) -> ClientWithdrawalContext | None: ...

    async def enqueue(self, command: EnqueueFulfillment) -> FulfillmentQueueItem: ...

    async def list_active(self) -> list[FulfillmentQueueItem]: ...

    async def reorder(self, command: ReorderFulfillment) -> FulfillmentQueueItem: ...

    async def summary(self) -> FulfillmentQueueSummary: ...

    async def cancel(
        self,
        *,
        item_id: int,
        reason: str,
    ) -> FulfillmentQueueItem: ...

    async def next_for_client_for_update(
        self,
        *,
        chat_id: int,
    ) -> FulfillmentQueueItem | None: ...

    async def executing_qty(self) -> Decimal: ...

    async def mark_executing(
        self,
        *,
        item_id: int,
        watch_id: int,
    ) -> FulfillmentQueueItem: ...

    async def get_by_deal_for_update(
        self,
        *,
        deal_id: int,
    ) -> FulfillmentQueueItem | None: ...

    async def complete(
        self,
        *,
        item_id: int,
        payment_event_id: int,
        position_move_id: int | None,
        actor_user_id: int | None,
        wallet_source: str,
    ) -> FulfillmentQueueItem: ...
