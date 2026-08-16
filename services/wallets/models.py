from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(slots=True, frozen=True)
class WalletCommandResult:
    ok: bool
    message_text: str
    reply_markup: object | None = None


@dataclass(slots=True, frozen=True)
class ParsedCurrencyChange:
    code: str
    expr: str
    amount: Decimal
    tail: str
    is_city_cash: bool
    client_name_for_transfer: str
    extra_comment: str


@dataclass(slots=True, frozen=True)
class CurrencyChangeCommand:
    chat_id: int
    chat_name: str
    message_id: int
    parsed: ParsedCurrencyChange
