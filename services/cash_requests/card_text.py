from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CardDataDepWd:
    kind: str  # "dep" | "wd"
    req_id: str  # "Б-123456"
    city: str
    code: str  # "RUB"/"USD"/...
    pretty_amount: str  # "150000.00"
    tg_from: str  # "Выдает"
    tg_to: str  # "Принимает"
    pin_code: str  # "123-456"
    comment: str = ""


@dataclass(frozen=True, slots=True)
class CardDataFx:
    req_id: str
    city: str
    in_code: str
    out_code: str
    pretty_in: str
    pretty_out: str
    tg_from: str
    tg_to: str
    pin_code: str
    comment: str = ""


def _req_title(kind: str) -> str:
    if kind == "dep":
        return "Заявка на внесение"
    if kind == "wd":
        return "Заявка на выдачу"
    return "Заявка на обмен"


def build_client_card_dep_wd(data: CardDataDepWd) -> str:
    """
    Карточка клиенту: без audit, код в spoiler, без кнопок.
    """
    title = _req_title(data.kind)
    lines: list[str] = [
        f"<b>{title}</b>: <code>{html.escape(data.req_id)}</code>",
        f"<b>Город</b>: <code>{html.escape(data.city)}</code>",
        "-----",
        f"<b>Сумма</b>: <code>{html.escape(data.pretty_amount)} {html.escape(data.code.lower())}</code>",
    ]
    if data.tg_to:
        lines.append(f"<b>Принимает</b>: {data.tg_to}")
    if data.tg_from:
        lines.append(f"<b>Выдает</b>: {data.tg_from}")
    lines.append(f"<b>Код</b>: <code>{html.escape(data.pin_code)}</code>")
    if data.comment:
        lines += ["----", f"<b>Комментарий</b>: <code>{html.escape(data.comment)}</code>❗️"]

    return "\n".join(lines)


def build_city_card_dep_wd(
    data: CardDataDepWd,
    *,
    chat_name: str,
    audit_lines: Sequence[str],
    changed_notice: bool = False,
) -> str:
    """
    Карточка в чат заявок города: код без spoiler, с audit и кнопками сделки.
    """
    title = _req_title(data.kind)
    lines: list[str] = []
    if changed_notice:
        lines += ["⚠️ <b>Внимание: заявка изменена.</b>", ""]

    lines += [
        f"<b>{title}</b>: <code>{html.escape(data.req_id)}</code>",
        f"<b>Город</b>: <code>{html.escape(data.city)}</code>",
        f"<b>Клиент</b>: <code>{html.escape(chat_name)}</code>",
        "-----",
        f"<b>Сумма</b>: <code>{html.escape(data.pretty_amount)} {html.escape(data.code.lower())}</code>",
    ]

    if data.tg_to:
        lines.append(f"<b>Принимает</b>: {data.tg_to}")
    if data.tg_from:
        lines.append(f"<b>Выдает</b>: {data.tg_from}")

    lines.append(f"<b>Код</b>: {html.escape(data.pin_code)}")

    if data.comment:
        lines += ["----", f"<b>Комментарий</b>: <code>{html.escape(data.comment)}</code>❗️"]

    if changed_notice:
        lines += ["----", "✏️ <b>Изменение заявки</b>"]

    lines += list(audit_lines)

    return "\n".join(lines)


def build_client_card_fx(data: CardDataFx) -> str:
    """
    FX клиенту: без audit, без кнопок.
    """
    client_contact = data.tg_from.strip() if (data.tg_from or "").strip() else "—"
    cashier = data.tg_to.strip() if (data.tg_to or "").strip() else "—"

    lines: list[str] = [
        f"<b>Заявка на обмен</b>: <code>{html.escape(data.req_id)}</code>",
        "-----",
        f"<b>Клиент</b>: {html.escape(client_contact)}",
        "",
        f"<b>Принимаем</b>: <code>{html.escape(data.pretty_in)} {html.escape(data.in_code)}</code>",
        f"<b>Выдаем</b>: <code>{html.escape(data.pretty_out)} {html.escape(data.out_code)}</code>",
        "",
        f"<b>Кассир</b>: {html.escape(cashier)}",
        f"<b>Код</b>: <code>{html.escape(data.pin_code)}</code>",
    ]
    if data.comment:
        lines += ["----", f"<b>Комментарий</b>: <code>{html.escape(data.comment)}</code>❗️"]

    return "\n".join(lines)


def build_city_card_fx(
    data: CardDataFx,
    *,
    chat_name: str,
    audit_lines: Sequence[str],
    changed_notice: bool = False,
) -> str:
    """
    FX в чат заявок города: с audit и кнопками сделки.
    """
    lines: list[str] = []
    if changed_notice:
        lines += ["⚠️ <b>Внимание: заявка изменена.</b>", ""]

    client_line = f"<code>{html.escape(chat_name)}</code>"
    if (data.tg_from or "").strip():
        client_line = f"{client_line} / {html.escape(data.tg_from.strip())}"

    cashier = data.tg_to.strip() if (data.tg_to or "").strip() else "—"

    lines += [
        f"<b>Заявка на обмен</b>: <code>{html.escape(data.req_id)}</code>",
        "-----",
        f"<b>Клиент</b>: {client_line}",
        "",
        f"<b>Принимаем</b>: <code>{html.escape(data.pretty_in)} {html.escape(data.in_code)}</code>",
        f"<b>Выдаем</b>: <code>{html.escape(data.pretty_out)} {html.escape(data.out_code)}</code>",
        "",
        f"<b>Кассир</b>: {html.escape(cashier)}",
        f"<b>Код</b>: {html.escape(data.pin_code)}",
    ]

    if data.comment:
        lines += ["----", f"<b>Комментарий</b>: <code>{html.escape(data.comment)}</code>❗️"]

    if changed_notice:
        lines += ["----", "✏️ <b>Изменение заявки</b>"]

    lines += list(audit_lines)

    return "\n".join(lines)
