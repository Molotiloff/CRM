from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum

from .errors import DomainValidationError


@dataclass(frozen=True, slots=True, order=True)
class CurrencyCode:
    value: str

    def __post_init__(self) -> None:
        normalized = str(self.value).strip().upper()
        if (
            not normalized
            or len(normalized) > 12
            or re.fullmatch(r"[A-Z0-9]+(?:_[A-Z0-9]+)*", normalized) is None
        ):
            raise DomainValidationError(f"Invalid currency code: {self.value!r}")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class CityCode:
    value: str

    def __post_init__(self) -> None:
        normalized = " ".join(str(self.value).strip().lower().split())
        if not normalized or len(normalized) > 64:
            raise DomainValidationError(f"Invalid city: {self.value!r}")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: CurrencyCode | str
    precision: int

    def __post_init__(self) -> None:
        amount = _decimal(self.amount, field="money amount")
        currency = (
            self.currency
            if isinstance(self.currency, CurrencyCode)
            else CurrencyCode(self.currency)
        )
        if not 0 <= self.precision <= 8:
            raise DomainValidationError("Money precision must be between 0 and 8")
        quantum = Decimal(1).scaleb(-self.precision)
        object.__setattr__(self, "amount", amount.quantize(quantum, rounding=ROUND_HALF_UP))
        object.__setattr__(self, "currency", currency)

    @classmethod
    def from_raw(
        cls,
        amount: object,
        currency: CurrencyCode | str,
        *,
        precision: int | None = None,
    ) -> Money:
        parsed = _decimal(amount, field="money amount")
        resolved_precision = (
            max(0, min(8, -parsed.as_tuple().exponent))
            if precision is None
            else precision
        )
        return cls(
            amount=parsed,
            currency=(
                currency if isinstance(currency, CurrencyCode) else CurrencyCode(currency)
            ),
            precision=resolved_precision,
        )

    def require_positive(self) -> Money:
        if self.amount <= 0:
            raise DomainValidationError("Money amount must be greater than zero")
        return self


@dataclass(frozen=True, slots=True)
class TelegramMessageRef:
    chat_id: int
    message_id: int

    def __post_init__(self) -> None:
        try:
            chat_id = int(self.chat_id)
            message_id = int(self.message_id)
        except (TypeError, ValueError):
            raise DomainValidationError("Invalid Telegram message reference") from None
        if chat_id == 0:
            raise DomainValidationError("Telegram chat id must not be zero")
        if message_id <= 0:
            raise DomainValidationError("Telegram message id must be greater than zero")
        object.__setattr__(self, "chat_id", chat_id)
        object.__setattr__(self, "message_id", message_id)


class DealStatus(StrEnum):
    NEW = "new"
    FIXED = "fixed"
    BALANCE_CHECK = "balance_check"
    AWAITING_PAYMENT = "awaiting_payment"
    IN_DELIVERY = "in_delivery"
    READY_FOR_CASH_SETTLEMENT = "ready_for_cash_settlement"
    DONE = "done"
    CANCELED = "canceled"


class DealSource(StrEnum):
    CRM = "crm"
    TELEGRAM = "tg_bot"
    IMPORT = "import"


class SourceKind(StrEnum):
    EXCHANGE = "exchange"
    CASH = "cash"
    FULFILLMENT = "fulfillment"
    PARTNER = "partner"


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise DomainValidationError(f"Invalid {field}: {value!r}") from None
    if not result.is_finite():
        raise DomainValidationError(f"Invalid {field}: {value!r}")
    return result
