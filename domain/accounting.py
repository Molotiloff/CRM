from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .errors import DomainStateError, DomainValidationError
from .value_objects import CurrencyCode

FIRM_POSITION_CURRENCIES = frozenset({"EUR", "USDT", "USD_BL", "USD_WH"})


class FirmPositionMoveKind(StrEnum):
    OPENING = "opening"
    PURCHASE = "purchase"
    SALE = "sale"
    ADJUST = "adjust"
    REVERSAL = "reversal"
    PROFIT_CAPITALIZATION = "profit_capitalization"


def firm_position_currency(value: CurrencyCode | str) -> CurrencyCode:
    currency = value if isinstance(value, CurrencyCode) else CurrencyCode(value)
    if currency.value not in FIRM_POSITION_CURRENCIES:
        raise DomainValidationError(
            f"Unsupported firm position currency: {currency.value!r}"
        )
    return currency


def accounting_decimal(value: object, *, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise DomainValidationError(f"Invalid {field}: {value!r}") from None
    if not result.is_finite():
        raise DomainValidationError(f"Invalid {field}: {value!r}")
    return result


@dataclass(frozen=True, slots=True)
class FirmPosition:
    currency: CurrencyCode
    qty: Decimal
    rub_cost: Decimal

    def __post_init__(self) -> None:
        currency = firm_position_currency(self.currency)
        qty = accounting_decimal(self.qty, field="position quantity")
        rub_cost = accounting_decimal(self.rub_cost, field="position rub cost")
        if qty < 0:
            raise DomainStateError("Firm position quantity must not be negative")
        if rub_cost < 0:
            raise DomainStateError("Firm position rub cost must not be negative")
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "qty", qty)
        object.__setattr__(self, "rub_cost", rub_cost)

    @property
    def average_rate(self) -> Decimal:
        return self.rub_cost / self.qty if self.qty else Decimal(0)


@dataclass(frozen=True, slots=True)
class NewFirmPositionMove:
    currency: CurrencyCode
    kind: FirmPositionMoveKind
    qty: Decimal
    rub_amount: Decimal
    qty_after: Decimal
    rub_cost_after: Decimal
    idempotency_key: str
    effective_at: datetime
    deal_id: int | None = None
    deal_leg_id: int | None = None
    entry_rate: Decimal | None = None
    created_by: int | None = None
    reason: str | None = None
    reversal_of_id: int | None = None

    def __post_init__(self) -> None:
        currency = firm_position_currency(self.currency)
        key = str(self.idempotency_key).strip()
        reason = str(self.reason).strip() if self.reason is not None else None
        if not key:
            raise DomainValidationError("Position idempotency key is required")
        if self.effective_at.tzinfo is None:
            raise DomainValidationError("Position effective_at must be timezone-aware")
        if self.kind is FirmPositionMoveKind.ADJUST and not reason:
            raise DomainValidationError("Position adjustment reason is required")
        if self.kind is FirmPositionMoveKind.REVERSAL:
            if self.reversal_of_id is None:
                raise DomainValidationError("Reversal source move is required")
            if not reason:
                raise DomainValidationError("Position reversal reason is required")
        FirmPosition(currency, self.qty_after, self.rub_cost_after)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "qty", accounting_decimal(self.qty, field="move quantity"))
        object.__setattr__(
            self,
            "rub_amount",
            accounting_decimal(self.rub_amount, field="move rub amount"),
        )
        object.__setattr__(
            self,
            "qty_after",
            accounting_decimal(self.qty_after, field="position quantity after move"),
        )
        object.__setattr__(
            self,
            "rub_cost_after",
            accounting_decimal(self.rub_cost_after, field="position rub cost after move"),
        )
        if self.entry_rate is not None:
            object.__setattr__(
                self,
                "entry_rate",
                accounting_decimal(self.entry_rate, field="position entry rate"),
            )
        object.__setattr__(self, "idempotency_key", key)
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True, slots=True)
class FirmPositionMove:
    id: int
    currency: CurrencyCode
    kind: FirmPositionMoveKind
    qty: Decimal
    rub_amount: Decimal
    qty_after: Decimal
    rub_cost_after: Decimal
    idempotency_key: str
    effective_at: datetime
    created_at: datetime
    deal_id: int | None = None
    deal_leg_id: int | None = None
    entry_rate: Decimal | None = None
    created_by: int | None = None
    reason: str | None = None
    reversal_of_id: int | None = None

    @property
    def position(self) -> FirmPosition:
        return FirmPosition(self.currency, self.qty_after, self.rub_cost_after)
