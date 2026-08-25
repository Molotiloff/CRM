from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from domain import CurrencyCode


@dataclass(frozen=True, slots=True, kw_only=True)
class CashChatBinding:
    city: str
    chat_id: int
    location_name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CashChatRegistrySyncResult:
    configured: int
    inserted: int
    reactivated: int
    deactivated: int


class WalletFactSource(StrEnum):
    MANUAL = "manual"
    PAYMENT_WATCH = "payment_watch"
    TRONSCAN = "tronscan"
    CASH_CHAT_LEDGER = "cash_chat_ledger"
    IMPORT = "import"


@dataclass(frozen=True, slots=True, kw_only=True)
class FirmWalletAddress:
    id: int
    network: str
    address: str
    active_from: datetime
    active_to: datetime | None
    reason: str
    created_by: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class WalletFactSnapshot:
    id: int
    currency: CurrencyCode
    actual_qty: Decimal
    observed_at: datetime
    source: WalletFactSource
    address_id: int | None
    actor_user_id: int | None
    comment: str | None
    idempotency_key: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class RotateFirmWalletAddress:
    network: str
    address: str
    active_from: datetime
    reason: str
    actor_user_id: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordWalletFact:
    currency: CurrencyCode | str
    actual_qty: Decimal
    observed_at: datetime
    source: WalletFactSource
    idempotency_key: str
    network: str | None = None
    actor_user_id: int | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MainDashboardCurrency:
    code: str
    free_qty: Decimal
    internal_rate: Decimal
    rub_cost: Decimal
    client_qty: Decimal
    deal_profit_qty: Decimal
    fact_qty: Decimal
    observed_qty: Decimal | None
    gap: Decimal | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True, kw_only=True)
class MainDashboardReconciliation:
    rub_cash: Decimal
    rub_in_currency: Decimal
    total_rub: Decimal
    client_balances: Decimal
    skyex_balances: Decimal
    total_balances: Decimal
    fact_turnover: Decimal
    accumulated_profit: Decimal
    invested_capital: Decimal
    turnover: Decimal
    gap: Decimal
    fact_rub: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class MainDashboardPeriods:
    daily_income: Decimal
    daily_expense: Decimal
    daily_profit: Decimal
    daily_turnover: Decimal
    monthly_turnover: Decimal
    profitability: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class MainDashboardCity:
    city: str
    income: Decimal
    expense: Decimal
    profit: Decimal
    active_requests: int


@dataclass(frozen=True, slots=True, kw_only=True)
class MainDashboardOperations:
    active_requests: int | None
    clients_with_balance: int | None
    queued_usdt_qty: Decimal
    queue_shortage_qty: Decimal
    onchain_liquid_qty: Decimal | None
    profit_in_transit_qty: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class MainDashboardSnapshot:
    source: str
    calculated_at: datetime
    data_as_of: datetime
    warnings: tuple[str, ...]
    currencies: tuple[MainDashboardCurrency, ...]
    reconciliation: MainDashboardReconciliation
    periods: MainDashboardPeriods
    cities: tuple[MainDashboardCity, ...]
    operations: MainDashboardOperations


@dataclass(frozen=True, slots=True, kw_only=True)
class PositionCommand:
    currency: CurrencyCode | str
    idempotency_key: str
    effective_at: datetime | None = None
    deal_id: int | None = None
    deal_leg_id: int | None = None
    actor_user_id: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordOpening(PositionCommand):
    qty: Decimal
    rub_cost: Decimal
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordPurchase(PositionCommand):
    qty: Decimal
    rate: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordSale(PositionCommand):
    qty: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordAdjustment(PositionCommand):
    qty_delta: Decimal
    rub_cost_delta: Decimal
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReversePositionMove(PositionCommand):
    move_id: int
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordProfitCapitalization(PositionCommand):
    qty: Decimal
    rate: Decimal


class PartnerAllocationDestination(StrEnum):
    FIRM_WALLET = "firm_wallet"
    CLIENT_DIRECT = "client_direct"


class PartnerAllocationStatus(StrEnum):
    ACTIVE = "active"
    SETTLED = "settled"
    REVERSED = "reversed"


@dataclass(frozen=True, slots=True, kw_only=True)
class PartnerPurchaseContext:
    deal_id: int
    qty: Decimal
    rate: Decimal
    currency: CurrencyCode


@dataclass(frozen=True, slots=True, kw_only=True)
class PartnerAllocation:
    id: int
    purchase_deal_id: int
    sale_deal_id: int | None
    destination: PartnerAllocationDestination
    qty: Decimal
    status: PartnerAllocationStatus
    idempotency_key: str
    transfer_watch_id: int | None = None
    position_move_id: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AllocatePartnerPurchase:
    purchase_deal_id: int
    qty: Decimal
    idempotency_key: str
    destination: PartnerAllocationDestination
    actor_user_id: int | None = None
    sale_deal_id: int | None = None
    network: str | None = None
    partner_address: str | None = None
    destination_address: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SettlePartnerTransfer:
    allocation_id: int
    actual_qty: Decimal
    tx_hash: str
    event_index: int
    confirmed_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketQuote:
    price: Decimal
    source: str
    symbol: str
    observed_at: datetime


class ProfitSettlementStatus(StrEnum):
    IN_TRANSIT = "in_transit"
    RECEIVED = "received"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfitAccrual:
    id: int
    deal_id: int
    qty: Decimal
    idempotency_key: str
    settlement_status: ProfitSettlementStatus
    created_at: datetime
    valuation_rate: Decimal | None = None
    capitalization_move_id: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AccrueUsdtProfit:
    deal_id: int
    qty: Decimal
    idempotency_key: str
    settlement_status: ProfitSettlementStatus = ProfitSettlementStatus.IN_TRANSIT


@dataclass(frozen=True, slots=True, kw_only=True)
class ManualProfitQuote:
    price: Decimal
    actor_user_id: int
    reason: str
    observed_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfitCapitalizationResult:
    business_date: date
    qty: Decimal
    rub_value: Decimal
    accrual_count: int
    position_move_id: int | None
    repeated: bool
