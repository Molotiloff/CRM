from __future__ import annotations

import html
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from services.exchange.text_builder import ExchangeTextBuilder

_STATUS_LINE_RE = re.compile(r"^<b>Статус(?: CRM)?</b>:.*$", re.MULTILINE)

STATUS_LABELS = {
    "new": "Новая",
    "fixed": "Курс зафиксирован",
    "balance_check": "Сверка баланса",
    "awaiting_payment": "Ожидаем оплату",
    "in_delivery": "Передано в доставку",
    "ready_for_cash_settlement": "Готово к расчету",
    "done": "Сделка завершена",
    "canceled": "Сделка отменена",
}

CLIENT_STATUS_LABELS = {
    **STATUS_LABELS,
    "ready_for_cash_settlement": "Готово к расчету",
    "done": "Проведена",
    "canceled": "Отменена",
}


class TelegramDealMessageBuilder:
    @classmethod
    def card_text(
        cls,
        base_text: str,
        *,
        status: str,
        payload: Mapping[str, Any],
        client_facing: bool = False,
    ) -> str:
        text = _STATUS_LINE_RE.sub("", base_text or "").rstrip()
        labels = CLIENT_STATUS_LABELS if client_facing else STATUS_LABELS
        label = "Статус" if client_facing else "Статус CRM"
        detail = None if client_facing else cls._detail(payload)
        line = f"<b>{label}</b>: <code>{html.escape(labels.get(status, status))}</code>"
        if detail:
            line += f" — {detail}"
        marker = "\n----\n<b>Создал</b>"
        index = text.find(marker)
        if index >= 0:
            return f"{text[:index]}\n{line}{text[index:]}"
        return f"{text}\n----\n{line}" if text else line

    @classmethod
    def notification(
        cls,
        *,
        request_id: str,
        status: str,
        payload: Mapping[str, Any],
    ) -> str:
        lines = [
            f"<b>Заявка</b>: <code>{html.escape(request_id)}</code>",
            f"<b>Новый статус</b>: {html.escape(STATUS_LABELS.get(status, status))}",
        ]
        detail = cls._detail(payload)
        if detail:
            lines.append(detail)
        return "\n".join(lines)

    @staticmethod
    def shortage_notification(
        *,
        request_id: str,
        current_usdt: object,
        shortage_usdt: object,
    ) -> str:
        return (
            "<b>На откуп: недостаточно USDT</b>\n"
            f"Заявка: <code>{html.escape(request_id)}</code>\n"
            f"USDT факт: <code>{html.escape(str(current_usdt))} USDT</code>\n"
            f"Не хватает: <code>{html.escape(str(shortage_usdt))} USDT</code>"
        )

    @staticmethod
    def exchange_client_base(body: Mapping[str, Any]) -> str | None:
        try:
            recv_amount = Decimal(str(body["recv_amount"]))
            pay_amount = Decimal(str(body["pay_amount"]))
            return ExchangeTextBuilder.build_client_text(
                req_id=str(body["client_req_id"]),
                recv_code=str(body["recv_code"]),
                recv_amount=recv_amount,
                recv_prec=_precision(recv_amount),
                pay_code=str(body["pay_code"]),
                pay_amount=pay_amount,
                pay_prec=_precision(pay_amount),
                rate=str(body["rate"]),
                note=str(body.get("note") or "").strip() or None,
            )
        except (KeyError, ValueError):
            return None

    @staticmethod
    def _detail(payload: Mapping[str, Any]) -> str | None:
        if payload.get("cashSettlementId"):
            amount = html.escape(str(payload.get("actualQty") or "0"))
            currency = html.escape(str(payload.get("currency") or ""))
            return f"<b>Фактически проведено</b>: <code>{amount} {currency}</code>"
        if payload.get("insufficientUsdt"):
            shortage = html.escape(str(payload.get("shortageUsdt") or "0"))
            return f"<b>На откуп</b>: не хватает <code>{shortage} USDT</code>"
        tronscan_url = str(payload.get("tronscanUrl") or "").strip()
        if tronscan_url:
            return f'<a href="{html.escape(tronscan_url, quote=True)}">Транзакция в Tronscan</a>'
        comment = str(payload.get("comment") or "").strip()
        if comment:
            return f"<b>Комментарий</b>: {html.escape(comment)}"
        rate = str(payload.get("rate") or "").strip()
        if rate:
            return f"<b>Курс</b>: <code>{html.escape(rate)}</code>"
        return None


def _precision(value: Decimal) -> int:
    return max(0, min(8, -value.as_tuple().exponent))
