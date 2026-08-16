from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .errors import DomainValidationError
from .value_objects import CityCode, Money, TelegramMessageRef


class ExchangeRequestStatus(StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class CashRequestKind(StrEnum):
    DEPOSIT = "dep"
    WITHDRAWAL = "wd"
    EXCHANGE = "fx"


@dataclass(frozen=True, slots=True)
class ExchangeRequestSource:
    request_id: str
    table_request_id: str
    receive: Money
    pay: Money
    rate: Decimal
    status: ExchangeRequestStatus
    client_message: TelegramMessageRef | None = None
    request_message: TelegramMessageRef | None = None
    request_text: str | None = None

    def __post_init__(self) -> None:
        request_id = self.request_id.strip()
        table_request_id = self.table_request_id.strip()
        rate = _required_decimal(self.rate, "exchange rate")
        try:
            status = ExchangeRequestStatus(str(self.status))
        except ValueError:
            raise DomainValidationError(
                f"Invalid exchange request status: {self.status!r}"
            ) from None
        if not request_id:
            raise DomainValidationError("Exchange request id must not be empty")
        if not table_request_id:
            raise DomainValidationError("Exchange table request id must not be empty")
        if rate <= 0:
            raise DomainValidationError("Exchange rate must be greater than zero")
        self.receive.require_positive()
        self.pay.require_positive()
        if self.client_message is None and self.request_message is None:
            raise DomainValidationError("Exchange Telegram message reference is missing")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "table_request_id", table_request_id)
        object.__setattr__(self, "rate", rate)
        object.__setattr__(self, "status", status)

    @property
    def primary_message(self) -> TelegramMessageRef:
        message = self.client_message or self.request_message
        if message is None:
            raise DomainValidationError("Exchange Telegram message reference is missing")
        return message

@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    request_id: str
    city: CityCode
    kind: CashRequestKind
    line_text: str
    client_name: str
    request_message: TelegramMessageRef
    hhmm: str | None = None

    def __post_init__(self) -> None:
        request_id = self.request_id.strip()
        line_text = self.line_text.strip()
        client_name = self.client_name.strip()
        city = self.city if isinstance(self.city, CityCode) else CityCode(self.city)
        try:
            kind = CashRequestKind(str(self.kind))
        except ValueError:
            raise DomainValidationError(
                f"Invalid schedule request kind: {self.kind!r}"
            ) from None
        if not request_id:
            raise DomainValidationError("Schedule request id must not be empty")
        if not line_text:
            raise DomainValidationError("Schedule line must not be empty")
        if not client_name:
            raise DomainValidationError("Schedule client must not be empty")
        if self.hhmm is not None:
            try:
                time.fromisoformat(self.hhmm)
            except ValueError:
                raise DomainValidationError(
                    f"Invalid schedule time: {self.hhmm!r}"
                ) from None
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "line_text", line_text)
        object.__setattr__(self, "client_name", client_name)
        object.__setattr__(self, "city", city)
        object.__setattr__(self, "kind", kind)


def _required_decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise DomainValidationError(f"Missing {field}") from None
    if not result.is_finite():
        raise DomainValidationError(f"Invalid {field}")
    return result
