from __future__ import annotations

from collections.abc import Mapping
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
from .import_models import ImportedRecord, ImportRunState
from .models import (
    CashChatBinding,
    CashChatRegistrySyncResult,
    FirmWalletAddress,
    MainDashboardSnapshot,
    ManualCashAccount,
    ManualCashMove,
    MarketQuote,
    PartnerAllocation,
    PartnerPurchaseContext,
    ProfitAccrual,
    RecordManualCashMove,
    ReverseManualCashMove,
    WalletFactSnapshot,
    WalletFactSource,
)


class ManualCashRepositoryPort(Protocol):
    async def snapshot(
        self, *, limit: int = 20
    ) -> tuple[tuple[ManualCashAccount, ...], tuple[ManualCashMove, ...]]: ...

    async def record(self, command: RecordManualCashMove) -> ManualCashMove: ...

    async def reverse(self, command: ReverseManualCashMove) -> ManualCashMove: ...


class AccountingImportRepositoryPort(Protocol):
    async def acquire_source_lock(self, source_name: str) -> None: ...

    async def start_or_resume(
        self, *, source_name: str, manifest_checksum: str, record_count: int,
        strategy: dict[str, str], control_totals: dict[str, str],
    ) -> ImportRunState: ...

    async def imported_record(
        self, *, source_name: str, entity_kind: str, source_key: str,
    ) -> ImportedRecord | None: ...

    async def record_applied(
        self, *, run_id: int, source_name: str, entity_kind: str, source_key: str,
        payload_checksum: str, sequence_no: int, target_table: str, target_id: int,
    ) -> None: ...

    async def advance_checkpoint(self, run_id: int, *, sequence_no: int) -> None: ...

    async def complete(self, run_id: int) -> None: ...

    async def fail(self, run_id: int, *, error_kind: str) -> None: ...

    async def resolve_client_id(self, chat_id: int) -> int: ...

    async def deal(
        self, *, deal_type: str, city: str, deal_at: date, profit: Decimal,
        body: Mapping[str, object], comment: str | None, source_ref: str,
    ) -> int: ...

    async def cash_registry(self, *, chat_id: int, city: str, location_name: str) -> int: ...

    async def internal_balance(
        self, *, name: str, kind: str, currency: str, amount: Decimal,
        idempotency_key: str,
    ) -> int: ...

    async def capital(
        self, *, owner: str, amount: Decimal, move_at: date,
        monthly_rate: Decimal | None, idempotency_key: str,
    ) -> int: ...

    async def expense(
        self, *, kind: str, category: str, city: str | None, amount: Decimal,
        expense_at: date, comment: str | None, idempotency_key: str,
    ) -> int: ...


class MarketQuoteProviderPort(Protocol):
    async def best_bid(self, symbol: str) -> MarketQuote | None: ...


class PartnerAllocationRepositoryPort(Protocol):
    async def acquire_purchase_lock(self, purchase_deal_id: int) -> None: ...

    async def purchase_context(self, purchase_deal_id: int) -> PartnerPurchaseContext | None: ...

    async def sale_quantity(self, sale_deal_id: int) -> Decimal | None: ...

    async def allocated_quantity(self, purchase_deal_id: int) -> Decimal: ...

    async def get_by_idempotency_key(self, key: str) -> PartnerAllocation | None: ...

    async def get_for_update(self, allocation_id: int) -> PartnerAllocation | None: ...

    async def has_non_reversed_sale_allocation(self, sale_deal_id: int) -> bool: ...

    async def create_firm_wallet_allocation(
        self, *, purchase_deal_id: int, qty: Decimal, idempotency_key: str,
        actor_user_id: int | None,
    ) -> PartnerAllocation: ...

    async def create_client_allocation(
        self, *, purchase_deal_id: int, sale_deal_id: int, qty: Decimal,
        network: str, partner_address: str, destination_address: str,
        idempotency_key: str, actor_user_id: int | None,
    ) -> PartnerAllocation: ...

    async def settle_firm_wallet(
        self, *, allocation_id: int, position_move_id: int,
    ) -> PartnerAllocation: ...

    async def settle_client_transfer(
        self, *, allocation_id: int, actual_qty: Decimal, tx_hash: str,
        event_index: int, confirmed_at: datetime,
    ) -> PartnerAllocation: ...

    async def record_partner_rub_balance(
        self, *, account_id: int, deal_id: int, amount: Decimal,
        actor_user_id: int | None, idempotency_key: str,
    ) -> int: ...


class ProfitAccrualRepositoryPort(Protocol):
    async def acquire_capitalization_lock(self, business_date: date) -> None: ...

    async def get_by_idempotency_key(self, key: str) -> ProfitAccrual | None: ...

    async def append(
        self, *, deal_id: int, qty: Decimal, settlement_status: str,
        idempotency_key: str, received_at: datetime | None = None,
    ) -> ProfitAccrual: ...

    async def list_pending_before(self, boundary: datetime) -> list[ProfitAccrual]: ...

    async def mark_capitalized(
        self, *, accrual_ids: tuple[int, ...], quote: MarketQuote,
        move_id: int, capitalized_at: datetime, actor_user_id: int | None,
        reason: str | None,
    ) -> None: ...

    async def mark_received(
        self, *, accrual_id: int, payment_event_id: int, received_at: datetime
    ) -> ProfitAccrual: ...


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

    async def active_user_id_by_tg_user_id(
        self,
        tg_user_id: int | None,
    ) -> int | None: ...

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
        qty: Decimal | None = None,
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
