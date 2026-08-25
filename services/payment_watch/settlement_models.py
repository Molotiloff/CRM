from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from domain import SettlementReviewStatus
from domain.accounting_flows import SettlementResolution


@dataclass(frozen=True, slots=True)
class ConfirmedTransfer:
    watch_id: int
    tx_hash: str
    direction: str
    amount: Decimal
    token_symbol: str
    confirmations: int
    block_ts: datetime
    from_address: str
    to_address: str


@dataclass(frozen=True, slots=True)
class SettlementContext:
    deal_id: int
    deal_status: str
    client_id: int
    client_chat_id: int | None
    request_chat_id: int | None
    recv_code: str
    recv_amount: Decimal
    pay_code: str
    pay_amount: Decimal


@dataclass(frozen=True, slots=True)
class PaymentEventClaim:
    event_id: int
    watch_id: int
    created: bool


@dataclass(frozen=True, slots=True)
class SettlementResult:
    settlement_id: int
    event_id: int
    deal_id: int
    expected: Decimal
    actual: Decimal
    delta: Decimal
    status: SettlementReviewStatus
    created: bool
    resolution: SettlementResolution | None = None
    evidence: ConfirmedTransfer | None = None


@dataclass(frozen=True, slots=True)
class ReplacementExchange:
    request_id: str
    table_request_id: str
    recv_code: str
    recv_amount: Decimal
    pay_code: str
    pay_amount: Decimal
    rate: Decimal

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.table_request_id.strip():
            raise ValueError("Replacement request ids are required")
        for value in (self.recv_amount, self.pay_amount, self.rate):
            if not value.is_finite() or value <= 0:
                raise ValueError("Replacement amounts and rate must be positive")


@dataclass(frozen=True, slots=True)
class ResolveSettlementCommand:
    settlement_id: int
    resolution: SettlementResolution
    actor_user_id: int
    comment: str | None = None
    replacement: ReplacementExchange | None = None


@dataclass(frozen=True, slots=True)
class SettlementReviewContext:
    result: SettlementResult
    event_direction: str
    deal_status: str
    client_id: int
    city: str
    source_ref: str
    request_id: str
    table_request_id: str
    recv_code: str
    recv_amount: Decimal
    pay_code: str
    pay_amount: Decimal
    rate: Decimal
    body: dict[str, Any]
