from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from domain import DealStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class CashSettlementCommand:
    request_id: str
    city_chat_id: int
    command_message_id: int
    currency: str
    signed_qty: Decimal
    actor_tg_user_id: int | None
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class CashSettlementContext:
    deal_id: int
    deal_status: DealStatus
    client_id: int
    cash_client_id: int
    request_id: str
    request_kind: str
    city: str
    currency: str
    expected_qty: Decimal
    track_client_balance: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class CashSettlementResult:
    settlement_id: int
    deal_id: int
    request_id: str
    request_kind: str
    currency: str
    actual_qty: Decimal
    cash_transaction_id: int
    client_transaction_id: int | None
    position_move_id: int | None
    repeated: bool
