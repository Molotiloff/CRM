from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain import CurrencyCode


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
