from __future__ import annotations

from dataclasses import dataclass

from domain import CashRequestKind
from services.cash_requests.card_text import (
    CardDataDepWd,
    CardDataFx,
    build_city_card_dep_wd,
    build_city_card_fx,
    build_client_card_dep_wd,
    build_client_card_fx,
)
from services.cash_requests.keyboard_port import CashKeyboardPort
from services.number_formatting import format_amount_core

from .workflow_models import CashRequestDetails


@dataclass(frozen=True, slots=True)
class CashCardPlan:
    client_text: str
    request_text: str
    request_markup: object
    schedule_line: str


class CashCardPresenter:
    def __init__(self, keyboards: CashKeyboardPort) -> None:
        self._keyboards = keyboards

    def build(
        self,
        *,
        details: CashRequestDetails,
        request_id: str,
        city: str,
        client_name: str,
        pin_code: str,
        contact1: str,
        contact2: str,
        comment: str,
        audit_lines: tuple[str, ...] | list[str],
        changed: bool,
    ) -> CashCardPlan:
        tg_from, tg_to = self._split_contacts(details.kind, contact1, contact2)
        if details.money is not None:
            money = details.money
            pretty = format_amount_core(money.amount, money.precision)
            data = CardDataDepWd(
                kind=str(details.kind),
                req_id=request_id,
                city=city,
                code=str(money.currency),
                pretty_amount=pretty,
                tg_from=tg_from,
                tg_to=tg_to,
                pin_code=pin_code,
                comment=comment,
            )
            client_text = build_client_card_dep_wd(data)
            request_text = build_city_card_dep_wd(
                data,
                chat_name=client_name,
                audit_lines=audit_lines,
                changed_notice=changed,
            )
            markup = self._keyboards.deal_actions(request_id=request_id)
            sign = "+" if details.kind is CashRequestKind.DEPOSIT else "-"
            schedule = f"{sign}{pretty} {money.currency} — {client_name}"
            return CashCardPlan(client_text, request_text, markup, schedule)

        receive = details.receive
        pay = details.pay
        assert receive is not None and pay is not None
        pretty_in = format_amount_core(receive.amount, receive.precision)
        pretty_out = format_amount_core(pay.amount, pay.precision)
        data_fx = CardDataFx(
            req_id=request_id,
            city=city,
            in_code=str(receive.currency),
            out_code=str(pay.currency),
            pretty_in=pretty_in,
            pretty_out=pretty_out,
            tg_from=tg_from,
            tg_to=tg_to,
            pin_code=pin_code,
            comment=comment,
        )
        client_text = build_client_card_fx(data_fx)
        request_text = build_city_card_fx(
            data_fx,
            chat_name=client_name,
            audit_lines=audit_lines,
            changed_notice=changed,
        )
        markup = self._keyboards.deal_actions(request_id=request_id)
        schedule = f"{pretty_in} {receive.currency} → {pretty_out} {pay.currency} — {client_name}"
        return CashCardPlan(client_text, request_text, markup, schedule)

    @staticmethod
    def _split_contacts(kind: CashRequestKind, contact1: str, contact2: str) -> tuple[str, str]:
        if kind in {CashRequestKind.DEPOSIT, CashRequestKind.EXCHANGE}:
            tg_to, tg_from = contact1, contact2
        else:
            tg_from, tg_to = contact1, contact2
        return (tg_from or "").strip(), (tg_to or "").strip()
