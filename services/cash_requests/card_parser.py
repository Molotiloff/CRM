from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from services.cash_requests.card_text_parser import (
    detect_kind_from_card,
    extract_edit_source,
    extract_time_from_card,
    parse_amount_code_line,
    parse_dep_wd_snapshot,
    parse_fx_snapshot,
)
from services.cash_requests.models import RequestEditSource

_RE_LINE_AMOUNT = re.compile(
    r"^\s*Сумма:\s*(?:<code>)?(.+?)(?:</code>)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_RE_LEGACY_KIND_AMOUNT = re.compile(
    r"^(Депозит|Выдача):\s*(?:<code>)?(.+?)(?:</code>)?\s*$",
    re.MULTILINE,
)
_AMOUNT_SEPARATORS = {" ", "\u00a0", "\u202f", "\u2009", "'", "’", "ʼ", "‛", "`"}


def _parse_legacy_kind_amount_code(text: str) -> tuple[str, Decimal, str] | None:
    match = _RE_LEGACY_KIND_AMOUNT.search(text)
    if match is None:
        return None
    try:
        raw_amount, raw_code = match.group(2).strip().rsplit(" ", 1)
    except ValueError:
        return None

    normalized_amount = raw_amount
    for separator in _AMOUNT_SEPARATORS:
        normalized_amount = normalized_amount.replace(separator, "")
    try:
        amount = Decimal(normalized_amount.replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None
    return match.group(1), amount, raw_code.strip().upper()


@dataclass(frozen=True, slots=True)
class CashEditCardSnapshot:
    source: RequestEditSource
    expected_codes: tuple[str, ...]
    hhmm: str | None


class CashCardParser:
    @staticmethod
    def edit_source(card_text: str) -> RequestEditSource | None:
        return extract_edit_source(card_text)

    @staticmethod
    def edit_snapshot(
        card_text: str,
        *,
        source: RequestEditSource,
        city: str,
    ) -> CashEditCardSnapshot | None:
        if source.kind in {"dep", "wd"}:
            snapshot = parse_dep_wd_snapshot(card_text, city=city)
            codes = (snapshot.code,) if snapshot else None
        else:
            snapshot = parse_fx_snapshot(card_text, city=city)
            codes = (snapshot.in_code, snapshot.out_code) if snapshot else None
        if codes is None:
            return None
        return CashEditCardSnapshot(
            source=source,
            expected_codes=codes,
            hhmm=extract_time_from_card(card_text),
        )

    @staticmethod
    def request_id(callback_data: str, *, prefix: str) -> str | None:
        data = (callback_data or "").strip()
        if not data.startswith(prefix):
            return None
        return data[len(prefix) :].strip() or None

    @staticmethod
    def issue_kind(callback_data: str, card_text: str, *, prefix: str) -> str | None:
        data = (callback_data or "").strip()
        prefix_with_separator = f"{prefix}:"
        kind = (
            data[len(prefix_with_separator) :].split(":", 1)[0].lower()
            if data.startswith(prefix_with_separator)
            else None
        )
        return kind or detect_kind_from_card(card_text)

    @staticmethod
    def amount_and_code(text: str, *, kind: str) -> tuple[Decimal, str] | None:
        amount_line = _RE_LINE_AMOUNT.search(text or "")
        if amount_line:
            return parse_amount_code_line(amount_line.group(1))

        legacy = _parse_legacy_kind_amount_code(text or "")
        if not legacy:
            return None
        kind_ru, amount, code = legacy
        expected_kind = {"dep": "Депозит", "wd": "Выдача"}.get(kind)
        if kind_ru != expected_kind:
            return None
        return amount, code
