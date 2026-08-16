from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from services.number_formatting import format_amount_core

from .card_parser import CANCEL_REQUEST_PREFIX


@dataclass(frozen=True, slots=True)
class CancelledBalanceLeg:
    code: str
    amount: Decimal
    precision: int
    operation_sign: str | None


class ExchangeNotificationBuilder:
    @staticmethod
    def edit_notice(request_id: str) -> str:
        return f"✏️ Заявка <code>{html.escape(request_id)}</code> была изменена"

    @staticmethod
    def cancelled_client_card(card_text: str, *, cancelled_at: datetime) -> str:
        timestamp = cancelled_at.strftime("%Y-%m-%d %H:%M")
        return f"{card_text}\n----\nОтмена: <code>{timestamp}</code>"

    @staticmethod
    def cancelled_request_card(request_text: str) -> str:
        if request_text.startswith(CANCEL_REQUEST_PREFIX):
            return request_text
        return f"{CANCEL_REQUEST_PREFIX}\n{request_text}"

    @staticmethod
    def table_delete_prompt(request_id: str, table_request_id: str) -> str:
        return (
            f"⛔️ Заявка <code>{html.escape(request_id)}</code> отменена.\n\n"
            "Удалить строки в Google Sheets (Покупка/Продажа) "
            f"с номером <b>{html.escape(table_request_id)}</b>?"
        )

    @staticmethod
    def cancellation_summary(
        request_id: str,
        accounts: Sequence[Mapping[str, Any]],
        legs: Sequence[CancelledBalanceLeg],
    ) -> str:
        lines = [f"⛔️ Заявка <code>{html.escape(request_id)}</code> отменена."]
        for leg in legs:
            if leg.operation_sign is None:
                continue
            account = next(
                (row for row in accounts if str(row["currency_code"]).upper() == leg.code.upper()),
                None,
            )
            operation = format_amount_core(leg.amount, leg.precision)
            balance = (
                format_amount_core(Decimal(str(account["balance"])), int(account["precision"]))
                if account
                else "—"
            )
            lines.extend(
                [
                    "",
                    f"Операция по {leg.code.lower()}: "
                    f"<code>{leg.operation_sign}{operation} {leg.code.lower()}</code>",
                    f"Баланс: <code>{balance} {leg.code.lower()}</code>",
                ]
            )
        return "\n".join(lines)
