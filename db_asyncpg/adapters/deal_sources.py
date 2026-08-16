from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from domain import (
    CashRequestKind,
    CityCode,
    DomainStateError,
    DomainValidationError,
    ExchangeRequestSource,
    ExchangeRequestStatus,
    Money,
    ScheduleEntry,
    TelegramMessageRef,
)


class ExchangeSourceRecordReader(Protocol):
    async def get_exchange_request_link(self, *, client_req_id: str) -> dict[str, Any] | None: ...


class ScheduleSourceRecordReader(Protocol):
    async def get_request_schedule_entry_by_req_id(
        self, *, req_id: str
    ) -> dict[str, Any] | None: ...


class DealSourceRecordReader(ExchangeSourceRecordReader, ScheduleSourceRecordReader, Protocol):
    pass


class DealSourceRepositoryAdapter:
    """Maps legacy persistence records to CRM domain source models."""

    def __init__(self, reader: DealSourceRecordReader) -> None:
        self._reader = reader

    async def get_exchange_source(self, *, request_id: str) -> ExchangeRequestSource | None:
        record = await self._reader.get_exchange_request_link(client_req_id=request_id)
        if record is None:
            return None
        try:
            return _exchange_source(record)
        except DomainValidationError as exc:
            raise DomainStateError(f"Invalid persisted exchange request {request_id}") from exc

    async def get_schedule_entry(self, *, request_id: str) -> ScheduleEntry | None:
        record = await self._reader.get_request_schedule_entry_by_req_id(req_id=request_id)
        if record is None:
            return None
        try:
            return _schedule_entry(record)
        except DomainValidationError as exc:
            raise DomainStateError(f"Invalid persisted cash schedule {request_id}") from exc


class DealSourceRecordReaderAdapter:
    def __init__(
        self,
        exchange_reader: ExchangeSourceRecordReader,
        schedule_reader: ScheduleSourceRecordReader,
    ) -> None:
        self._exchange_reader = exchange_reader
        self._schedule_reader = schedule_reader

    async def get_exchange_request_link(self, *, client_req_id: str) -> dict[str, Any] | None:
        return await self._exchange_reader.get_exchange_request_link(client_req_id=client_req_id)

    async def get_request_schedule_entry_by_req_id(self, *, req_id: str) -> dict[str, Any] | None:
        return await self._schedule_reader.get_request_schedule_entry_by_req_id(req_id=req_id)


def _exchange_source(record: Mapping[str, Any]) -> ExchangeRequestSource:
    try:
        status = ExchangeRequestStatus(
            _required_text(record.get("status") or "active", "exchange status")
        )
    except ValueError as exc:
        raise DomainValidationError("Invalid exchange request status") from exc
    return ExchangeRequestSource(
        request_id=_required_text(record.get("client_req_id"), "exchange request id"),
        table_request_id=_required_text(record.get("table_req_id"), "exchange table request id"),
        receive=Money.from_raw(
            record.get("table_in_amount"),
            _required_text(record.get("table_in_cur"), "exchange receive currency"),
        ),
        pay=Money.from_raw(
            record.get("table_out_amount"),
            _required_text(record.get("table_out_cur"), "exchange pay currency"),
        ),
        rate=_required_decimal(record.get("table_rate"), "exchange rate"),
        status=status,
        client_message=_message_ref(record.get("client_chat_id"), record.get("client_message_id")),
        request_message=_message_ref(
            record.get("request_chat_id"), record.get("request_message_id")
        ),
        request_text=(str(record["request_text"]) if record.get("request_text") else None),
    )


def _schedule_entry(record: Mapping[str, Any]) -> ScheduleEntry:
    try:
        kind = CashRequestKind(_required_text(record.get("request_kind"), "schedule request kind"))
    except ValueError as exc:
        raise DomainValidationError("Invalid schedule request kind") from exc
    return ScheduleEntry(
        request_id=_required_text(record.get("req_id"), "schedule request id"),
        city=CityCode(_required_text(record.get("city"), "schedule city")),
        kind=kind,
        line_text=_required_text(record.get("line_text"), "schedule line"),
        client_name=_required_text(record.get("client_name"), "schedule client"),
        request_message=TelegramMessageRef(
            chat_id=_required_int(record.get("request_chat_id"), "schedule chat id"),
            message_id=_required_int(record.get("request_message_id"), "schedule message id"),
        ),
        hhmm=str(record["hhmm"]) if record.get("hhmm") else None,
    )


def _message_ref(chat_id: object, message_id: object) -> TelegramMessageRef | None:
    if chat_id is None and message_id is None:
        return None
    if chat_id is None or message_id is None:
        raise DomainValidationError("Incomplete Telegram message reference")
    return TelegramMessageRef(
        chat_id=_required_int(chat_id, "chat id"),
        message_id=_required_int(message_id, "message id"),
    )


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DomainValidationError(f"Missing {field}")
    return text


def _required_int(value: object, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise DomainValidationError(f"Missing {field}") from None


def _required_decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise DomainValidationError(f"Missing {field}") from None
    if not result.is_finite():
        raise DomainValidationError(f"Invalid {field}")
    return result
