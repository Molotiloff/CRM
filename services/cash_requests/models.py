from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(slots=True, frozen=True)
class RequestContext:
    city: str
    request_chat_id: int | None
    chat_name: str
    client_id: int


@dataclass(slots=True, frozen=True)
class RequestEditSource:
    req_id: str
    pin_code: str
    kind: str
    old_text: str


@dataclass(slots=True, frozen=True)
class DepWdCardSnapshot:
    req_id: str
    kind: str
    city: str
    code: str
    amount: Decimal
    pin_code: str


@dataclass(slots=True, frozen=True)
class FxCardSnapshot:
    req_id: str
    city: str
    in_code: str
    out_code: str
    amt_in: Decimal
    amt_out: Decimal
    pin_code: str


@dataclass(slots=True, frozen=True)
class ScheduleEntry:
    req_id: str
    city: str
    hhmm: str | None
    request_kind: str
    line_text: str
    client_name: str
    request_chat_id: int
    request_message_id: int
