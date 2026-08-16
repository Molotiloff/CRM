from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from domain import CityCode, CurrencyCode, DomainValidationError, Money


class DealSourceEditCommand:
    """Marker base for source-specific CRM edit commands."""


@dataclass(frozen=True, slots=True)
class ExchangeSourceEdit(DealSourceEditCommand):
    operation_id: int
    recv_code: CurrencyCode | str
    recv_amount: Decimal
    pay_code: CurrencyCode | str
    pay_amount: Decimal
    rate: Decimal
    note: str | None = None

    def __post_init__(self) -> None:
        receive = Money.from_raw(self.recv_amount, self.recv_code).require_positive()
        pay = Money.from_raw(self.pay_amount, self.pay_code).require_positive()
        object.__setattr__(self, "recv_code", receive.currency)
        object.__setattr__(self, "recv_amount", receive.amount)
        object.__setattr__(self, "pay_code", pay.currency)
        object.__setattr__(self, "pay_amount", pay.amount)
        object.__setattr__(self, "rate", _positive_decimal(self.rate, "exchange rate"))

    @property
    def receive(self) -> Money:
        return Money.from_raw(self.recv_amount, self.recv_code)

    @property
    def pay(self) -> Money:
        return Money.from_raw(self.pay_amount, self.pay_code)


@dataclass(frozen=True, slots=True)
class CashSourceEdit(DealSourceEditCommand):
    city: CityCode | str
    amount: Decimal | None = None
    in_amount: Decimal | None = None
    out_amount: Decimal | None = None
    comment: str | None = None
    contact1: str = ""
    contact2: str = ""

    def __post_init__(self) -> None:
        city = self.city if isinstance(self.city, CityCode) else CityCode(self.city)
        object.__setattr__(self, "city", city)
        for field in ("amount", "in_amount", "out_amount"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _positive_decimal(value, field))


def _positive_decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise DomainValidationError(f"Invalid {field}") from None
    if not result.is_finite() or result <= 0:
        raise DomainValidationError(f"Invalid {field}")
    return result
