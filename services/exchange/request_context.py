from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from domain import DomainStateError, TelegramMessageRef


@dataclass(frozen=True, slots=True)
class ExchangeRequestContext:
    table_request_id: str | None
    request_message: TelegramMessageRef | None
    request_text: str | None
    table_done: bool
    client_message: TelegramMessageRef | None = None

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> ExchangeRequestContext:
        return cls(
            table_request_id=_optional_text(record.get("table_req_id")),
            request_message=_optional_message(
                record.get("request_chat_id"), record.get("request_message_id")
            ),
            request_text=_optional_text(record.get("request_text")),
            table_done=bool(record.get("is_table_done")),
            client_message=_optional_message(
                record.get("client_chat_id"), record.get("client_message_id")
            ),
        )


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_message(chat_id: object, message_id: object) -> TelegramMessageRef | None:
    if chat_id is None and message_id is None:
        return None
    if chat_id is None or message_id is None:
        raise DomainStateError("Incomplete exchange request Telegram reference")
    return TelegramMessageRef(int(chat_id), int(message_id))
