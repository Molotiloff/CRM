from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal

from services.number_formatting import format_amount_core


@dataclass(slots=True, frozen=True)
class ExchangeTexts:
    client_text: str
    request_text: str


class ExchangeTextBuilder:
    @staticmethod
    def build_client_text(
        *,
        req_id: int | str,
        recv_code: str,
        recv_amount: Decimal,
        recv_prec: int,
        pay_code: str,
        pay_amount: Decimal,
        pay_prec: int,
        rate: str,
        note: str | None = None,
        note_alert: bool = False,
        changed_at: str | None = None,
    ) -> str:
        pretty_recv = format_amount_core(recv_amount, recv_prec)
        pretty_pay = format_amount_core(pay_amount, pay_prec)
        lines = [
            f"<b>Заявка</b>: <code>{req_id}</code>",
            "-----",
            f"<b>Получаем</b>: <code>{pretty_recv} {recv_code.lower()}</code>",
            f"<b>Курс</b>: <code>{rate}</code>",
            f"<b>Отдаём</b>: <code>{pretty_pay} {pay_code.lower()}</code>",
        ]
        if note:
            alert = "❗️" if note_alert else ""
            lines += ["----", f"<b>Комментарий</b>: <code>{html.escape(note)}</code>{alert}"]
        if changed_at:
            lines += ["----", f"<b>Изменение</b>: <code>{changed_at}</code>"]
        return "\n".join(lines)

    @staticmethod
    def build_request_text(
        *,
        req_id: int | str,
        table_req_id: int | str,
        client_name: str,
        recv_code: str,
        recv_amount: Decimal,
        recv_prec: int,
        pay_code: str,
        pay_amount: Decimal,
        pay_prec: int,
        rate: str,
        creator_name: str,
        note: str | None = None,
        formula: str | None = None,
        changed_at: str | None = None,
    ) -> str:
        pretty_recv = format_amount_core(recv_amount, recv_prec)
        pretty_pay = format_amount_core(pay_amount, pay_prec)
        lines = [
            f"<b>Заявка</b>: <code>{req_id}</code>",
            f"<b>Номер в таблице</b>: <code>{html.escape(str(table_req_id))}</code>",
            f"<b>Клиент</b>: <b>{html.escape(client_name)}</b>",
            "-----",
            f"<b>Получаем</b>: <code>{pretty_recv} {recv_code.lower()}</code>",
            f"<b>Курс</b>: <code>{rate}</code>",
            f"<b>Отдаём</b>: <code>{pretty_pay} {pay_code.lower()}</code>",
        ]
        if note:
            lines += ["----", f"<b>Комментарий</b>: <code>{html.escape(note)}</code>❗️"]
        if changed_at:
            lines += ["----", f"Изменение: <code>{changed_at}</code>"]
        if formula is not None:
            lines += ["----", f"<b>Формула</b>: <code>{html.escape(formula)}</code>"]
        lines += ["----", f"<b>Создал</b>: <b>{html.escape(creator_name)}</b>"]
        return "\n".join(lines)

    @classmethod
    def build_new_texts(
        cls,
        *,
        req_id: int | str,
        table_req_id: int | str,
        client_name: str,
        recv_code: str,
        recv_amount: Decimal,
        recv_prec: int,
        pay_code: str,
        pay_amount: Decimal,
        pay_prec: int,
        rate: str,
        creator_name: str,
        note: str | None,
        formula: str,
    ) -> ExchangeTexts:
        return ExchangeTexts(
            client_text=cls.build_client_text(
                req_id=req_id,
                recv_code=recv_code,
                recv_amount=recv_amount,
                recv_prec=recv_prec,
                pay_code=pay_code,
                pay_amount=pay_amount,
                pay_prec=pay_prec,
                rate=rate,
                note=note,
                note_alert=True,
            ),
            request_text=cls.build_request_text(
                req_id=req_id,
                table_req_id=table_req_id,
                client_name=client_name,
                recv_code=recv_code,
                recv_amount=recv_amount,
                recv_prec=recv_prec,
                pay_code=pay_code,
                pay_amount=pay_amount,
                pay_prec=pay_prec,
                rate=rate,
                creator_name=creator_name,
                note=note,
                formula=formula,
            ),
        )
