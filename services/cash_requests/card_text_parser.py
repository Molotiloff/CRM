from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from services.cash_requests.models import DepWdCardSnapshot, FxCardSnapshot, RequestEditSource

_RE_REQ_ID_ANY = re.compile(
    r"^\s*Заявка(?:\s+на\s+(?:внесение|выдачу|обмен))?\s*:\s*(?:<code>)?([A-Za-zА-Яа-я0-9\-]+)(?:</code>)?\s*$",
    re.IGNORECASE | re.M,
)
_RE_LINE_PIN = re.compile(
    r"^\s*Код:\s*(?:<tg-spoiler>)?(\d{3}-\d{3})(?:</tg-spoiler>)?\s*$",
    re.IGNORECASE | re.M,
)
_RE_LINE_AMOUNT = re.compile(
    r"^\s*Сумма:\s*(?:<code>)?(.+?)(?:</code>)?\s*$",
    re.IGNORECASE | re.M,
)
_RE_LINE_IN = re.compile(
    r"^\s*Принимаем:\s*(?:<code>)?(.+?)(?:</code>)?\s*$",
    re.IGNORECASE | re.M,
)
_RE_LINE_OUT = re.compile(
    r"^\s*(?:Отдаем|Выдаем):\s*(?:<code>)?(.+?)(?:</code>)?\s*$",
    re.IGNORECASE | re.M,
)

_RE_TITLE_DEP = re.compile(r"^\s*Заявка\s+на\s+внесение\s*:", re.IGNORECASE | re.M)
_RE_TITLE_WD = re.compile(r"^\s*Заявка\s+на\s+выдачу\s*:", re.IGNORECASE | re.M)
_RE_TITLE_FX = re.compile(r"^\s*Заявка\s+на\s+обмен\s*:", re.IGNORECASE | re.M)
_RE_KIND_DEP_LEGACY = re.compile(r"Код\s+получения", re.IGNORECASE)
_RE_KIND_WD_LEGACY = re.compile(r"Код\s+выдачи", re.IGNORECASE)

_RE_LINE_CLIENT = re.compile(r"^\s*Клиент:\s*(.+?)\s*$", re.IGNORECASE | re.M)

_RE_LINE_TIME = re.compile(
    r"^\s*Время\s*:\s*(?:<code>)?([0-2]\d:[0-5]\d)(?:</code>)?\s*$",
    re.IGNORECASE | re.M,
)

# Главное изменение:
# теперь ищем строку заявки в любом месте текста, а не только в самом начале.
_RE_REQUEST_TITLE_ANYWHERE = re.compile(
    r"^[ \t]*(?:<[^>]+>[ \t]*)*Заявка(?:[ \t]+на[ \t]+(?:внесение|выдачу|обмен))?[ \t]*",
    re.IGNORECASE | re.M,
)

_SEP = {" ", "\u00a0", "\u202f", "\u2009", "'", "’", "ʼ", "‛", "`"}


def starts_with_request(text: str) -> bool:
    return _RE_REQUEST_TITLE_ANYWHERE.search(text or "") is not None


def detect_kind_from_card(text: str) -> str | None:
    if _RE_TITLE_DEP.search(text) or _RE_KIND_DEP_LEGACY.search(text):
        return "dep"
    if _RE_TITLE_WD.search(text) or _RE_KIND_WD_LEGACY.search(text):
        return "wd"
    if _RE_TITLE_FX.search(text):
        return "fx"
    return None


def extract_edit_source(old_text: str) -> RequestEditSource | None:
    m_req = _RE_REQ_ID_ANY.search(old_text)
    m_pin = _RE_LINE_PIN.search(old_text)
    if not (m_req and m_pin):
        return None

    kind = detect_kind_from_card(old_text)
    if not kind:
        return None

    return RequestEditSource(
        req_id=m_req.group(1),
        pin_code=m_pin.group(1),
        kind=kind,
        old_text=old_text,
    )


def parse_amount_code_line(blob: str) -> tuple[Decimal, str] | None:
    try:
        amt_str, code = blob.rsplit(" ", 1)
    except ValueError:
        return None

    for ch in _SEP:
        amt_str = amt_str.replace(ch, "")
    amt_str = amt_str.replace(",", ".").strip()

    try:
        amt = Decimal(amt_str)
    except InvalidOperation:
        return None

    return amt, code.strip().upper()


def parse_dep_wd_snapshot(old_text: str, *, city: str) -> DepWdCardSnapshot | None:
    src = extract_edit_source(old_text)
    if not src or src.kind not in ("dep", "wd"):
        return None

    m_amt = _RE_LINE_AMOUNT.search(old_text)
    if not m_amt:
        return None

    parsed = parse_amount_code_line(m_amt.group(1))
    if not parsed:
        return None

    amount, code = parsed
    return DepWdCardSnapshot(
        req_id=src.req_id,
        kind=src.kind,
        city=city,
        code=code,
        amount=amount,
        pin_code=src.pin_code,
    )


def parse_fx_snapshot(old_text: str, *, city: str) -> FxCardSnapshot | None:
    src = extract_edit_source(old_text)
    if not src or src.kind != "fx":
        return None

    m_in = _RE_LINE_IN.search(old_text)
    m_out = _RE_LINE_OUT.search(old_text)
    if not (m_in and m_out):
        return None

    parsed_in = parse_amount_code_line(m_in.group(1))
    parsed_out = parse_amount_code_line(m_out.group(1))
    if not parsed_in or not parsed_out:
        return None

    amt_in, in_code = parsed_in
    amt_out, out_code = parsed_out

    return FxCardSnapshot(
        req_id=src.req_id,
        city=city,
        in_code=in_code,
        out_code=out_code,
        amt_in=amt_in,
        amt_out=amt_out,
        pin_code=src.pin_code,
    )


def extract_time_from_card(text: str) -> str | None:
    m = _RE_LINE_TIME.search(text or "")
    return m.group(1) if m else None


def upsert_time_line(card_text: str, hhmm: str) -> str:
    text = card_text or ""
    new_line = f"Время: <code>{hhmm}</code>"

    if _RE_LINE_TIME.search(text):
        return _RE_LINE_TIME.sub(new_line, text)

    mk = "\n----\nСоздал:"
    idx = text.find(mk)
    if idx != -1:
        return text[:idx] + "\n" + new_line + text[idx:]

    last_sep = text.rfind("\n----")
    if last_sep != -1:
        return text[:last_sep] + "\n" + new_line + text[last_sep:]

    if text.endswith("\n"):
        return text + new_line
    return text + "\n" + new_line


def extract_client_name(text: str, fallback: str = "—") -> str:
    m = _RE_LINE_CLIENT.search(text or "")
    if not m:
        return fallback
    return (m.group(1) or "").strip() or fallback


def build_schedule_line_from_plain(text: str, *, fallback_client: str = "—") -> str | None:
    kind = detect_kind_from_card(text or "")
    if not kind:
        return None

    client_name = extract_client_name(text, fallback=fallback_client)

    if kind in ("dep", "wd"):
        m_amt = _RE_LINE_AMOUNT.search(text or "")
        if not m_amt:
            return None
        amount_blob = m_amt.group(1).strip()
        sign = "+" if kind == "dep" else "-"
        return f"{sign}{amount_blob.upper()} — {client_name}"

    m_in = _RE_LINE_IN.search(text or "")
    m_out = _RE_LINE_OUT.search(text or "")
    if not (m_in and m_out):
        return None

    return f"{m_in.group(1).strip().upper()} → {m_out.group(1).strip().upper()} — {client_name}"
