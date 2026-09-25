from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from .errors import DomainValidationError
from .value_objects import CityCode, DealSource, DealStatus, SourceKind


class DealType(StrEnum):
    SALE = "sale"
    PURCHASE = "purchase"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    DELIVERY = "delivery"
    TRANSFER_CITY = "transfer_city"
    CLIENT_TRANSFER = "client_transfer"
    CONVERSION = "conversion"
    YUAN = "yuan"
    INVOICE = "invoice"
    PROFIT = "profit"
    BEST_CHANGE = "best_change"


@dataclass(frozen=True, slots=True)
class _DomainMap:
    _values: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._values, Mapping):
            raise DomainValidationError(f"{type(self).__name__} must be an object")
        object.__setattr__(self, "_values", dict(self._values))

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def merged(self, patch: Mapping[str, Any]) -> Self:
        values = self.to_dict()
        values.update(patch)
        return type(self)(values)

    def __bool__(self) -> bool:
        return bool(self._values)


@dataclass(frozen=True, slots=True)
class DealBody(_DomainMap):
    pass


@dataclass(frozen=True, slots=True)
class DealEventPayload(_DomainMap):
    pass


@dataclass(frozen=True, slots=True)
class DealLeg:
    id: int
    direction: str
    currency_code: str
    amount: Decimal
    rate: Decimal | None
    status: str

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> DealLeg:
        return cls(
            id=_required_int(record.get("id"), "deal leg id"),
            direction=_required_text(record.get("direction"), "deal leg direction"),
            currency_code=_required_text(
                record.get("currency_code"), "deal leg currency"
            ).upper(),
            amount=_decimal(record.get("amount"), "deal leg amount"),
            rate=(
                _decimal(record.get("rate"), "deal leg rate")
                if record.get("rate") is not None
                else None
            ),
            status=_required_text(record.get("status"), "deal leg status"),
        )


@dataclass(frozen=True, slots=True)
class DealStatusEvent:
    id: int
    old_status: DealStatus | None
    new_status: DealStatus
    actor_name: str
    payload: DealEventPayload
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.payload, DealEventPayload):
            object.__setattr__(self, "payload", DealEventPayload(self.payload))

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> DealStatusEvent:
        old_status = record.get("old_status")
        return cls(
            id=_required_int(record.get("id"), "deal status event id"),
            old_status=_enum(DealStatus, old_status, "old deal status")
            if old_status is not None
            else None,
            new_status=_enum(
                DealStatus, record.get("new_status"), "new deal status"
            ),
            actor_name=str(record.get("actor_name") or "System"),
            payload=DealEventPayload(
                _mapping(record.get("payload"), "deal event payload")
            ),
            created_at=_optional_datetime(record.get("created_at")),
        )


@dataclass(frozen=True, slots=True)
class Deal:
    id: int
    deal_no: int
    deal_type: DealType
    city: CityCode
    status: DealStatus
    source: DealSource
    body: DealBody
    client_id: int | None = None
    counterparty_id: int | None = None
    created_by: int | None = None
    source_kind: SourceKind | None = None
    source_ref: str | None = None
    exchange_client_req_id: str | None = None
    client_name: str | None = None
    client_chat_id: int | None = None
    counterparty_name: str | None = None
    counterparty_percent: Decimal | None = None
    created_by_name: str = "System"
    comment: str | None = None
    penalty: Decimal | None = None
    tronscan_url: str | None = None
    profit: Decimal | None = None
    deal_at: date | None = None
    payment_watch_id: int | None = None
    payment_watch_status: str | None = None
    corrected_from_deal_id: int | None = None
    corrected_to_deal_id: int | None = None
    correction_reason: str | None = None
    correction_actor_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    legs: tuple[DealLeg, ...] = ()
    status_events: tuple[DealStatusEvent, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "deal_type",
            self.deal_type
            if isinstance(self.deal_type, DealType)
            else _enum(DealType, self.deal_type, "deal type"),
        )
        object.__setattr__(
            self,
            "city",
            self.city if isinstance(self.city, CityCode) else CityCode(self.city),
        )
        object.__setattr__(
            self,
            "status",
            self.status
            if isinstance(self.status, DealStatus)
            else _enum(DealStatus, self.status, "deal status"),
        )
        object.__setattr__(
            self,
            "source",
            self.source
            if isinstance(self.source, DealSource)
            else _enum(DealSource, self.source, "deal source"),
        )
        if self.source_kind is not None and not isinstance(
            self.source_kind, SourceKind
        ):
            object.__setattr__(
                self,
                "source_kind",
                _enum(SourceKind, self.source_kind, "deal source kind"),
            )
        if not isinstance(self.body, DealBody):
            object.__setattr__(self, "body", DealBody(self.body))

    @property
    def is_terminal(self) -> bool:
        return self.status in {DealStatus.DONE, DealStatus.CANCELED}

    @property
    def is_telegram(self) -> bool:
        return self.source is DealSource.TELEGRAM

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> Deal:
        return cls(
            id=_required_int(record.get("id"), "deal id"),
            deal_no=_required_int(record.get("deal_no"), "deal number"),
            deal_type=_enum(DealType, record.get("deal_type"), "deal type"),
            city=CityCode(_required_text(record.get("city"), "deal city")),
            status=_enum(DealStatus, record.get("status"), "deal status"),
            source=_enum(
                DealSource, record.get("source") or DealSource.CRM, "deal source"
            ),
            source_kind=(
                _enum(SourceKind, record.get("source_kind"), "deal source kind")
                if record.get("source_kind") is not None
                else None
            ),
            body=DealBody(_mapping(record.get("body"), "deal body")),
            client_id=_optional_int(record.get("client_id"), "deal client id"),
            counterparty_id=_optional_int(
                record.get("counterparty_id"), "deal counterparty id"
            ),
            created_by=_optional_int(record.get("created_by"), "deal creator id"),
            source_ref=_optional_text(record.get("source_ref")),
            exchange_client_req_id=_optional_text(
                record.get("exchange_client_req_id")
            ),
            client_name=_optional_text(record.get("client_name")),
            client_chat_id=_optional_int(
                record.get("client_chat_id"), "deal client chat id"
            ),
            counterparty_name=_optional_text(record.get("counterparty_name")),
            counterparty_percent=_optional_decimal(
                record.get("counterparty_percent"), "counterparty percent"
            ),
            created_by_name=str(record.get("created_by_name") or "System"),
            comment=_optional_text(record.get("comment")),
            penalty=_optional_decimal(record.get("penalty"), "deal penalty"),
            tronscan_url=_optional_text(record.get("tronscan_url")),
            profit=_optional_decimal(record.get("profit"), "deal profit"),
            deal_at=_optional_date(record.get("deal_at")),
            payment_watch_id=_optional_int(
                record.get("payment_watch_id"), "payment watch id"
            ),
            payment_watch_status=_optional_text(
                record.get("payment_watch_status")
            ),
            corrected_from_deal_id=_optional_int(
                record.get("corrected_from_deal_id"),
                "original corrected deal id",
            ),
            corrected_to_deal_id=_optional_int(
                record.get("corrected_to_deal_id"),
                "replacement corrected deal id",
            ),
            correction_reason=_optional_text(record.get("correction_reason")),
            correction_actor_name=_optional_text(
                record.get("correction_actor_name")
            ),
            created_at=_optional_datetime(record.get("created_at")),
            updated_at=_optional_datetime(record.get("updated_at")),
            legs=_legs(record.get("legs")),
            status_events=_status_events(record.get("status_events")),
        )


def _legs(value: object) -> tuple[DealLeg, ...]:
    records = _record_sequence(value, "deal legs")
    return tuple(DealLeg.from_record(record) for record in records)


def _status_events(value: object) -> tuple[DealStatusEvent, ...]:
    records = _record_sequence(value, "deal status events")
    return tuple(DealStatusEvent.from_record(record) for record in records)


def _record_sequence(value: object, field: str) -> Sequence[Mapping[str, Any]]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise DomainValidationError(f"Invalid {field}")
    if any(not isinstance(item, Mapping) for item in value):
        raise DomainValidationError(f"Invalid {field}")
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"Invalid {field}")
    return value


def _enum(enum_type, value: object, field: str):
    try:
        return enum_type(str(value))
    except ValueError:
        raise DomainValidationError(f"Invalid {field}: {value!r}") from None


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DomainValidationError(f"Missing {field}")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_int(value: object, field: str) -> int:
    result = _optional_int(value, field)
    if result is None:
        raise DomainValidationError(f"Missing {field}")
    return result


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise DomainValidationError(f"Invalid {field}: {value!r}") from None


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        raise DomainValidationError(f"Invalid {field}: {value!r}") from None
    if not result.is_finite():
        raise DomainValidationError(f"Invalid {field}: {value!r}")
    return result


def _optional_decimal(value: object, field: str) -> Decimal | None:
    return None if value is None else _decimal(value, field)


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise DomainValidationError(f"Invalid deal date: {value!r}") from None


def _optional_datetime(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        raise DomainValidationError(f"Invalid datetime: {value!r}") from None
