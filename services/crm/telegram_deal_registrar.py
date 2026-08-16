from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from domain import Deal

from .deal_service import DealCreateCommand, DealService


@dataclass(frozen=True, slots=True)
class ExchangeDealData:
    source_ref: str
    client_id: int
    client_req_id: str
    table_req_id: int
    client_name: str
    creator_name: str
    recv_code: str
    recv_amount: Decimal
    pay_code: str
    pay_amount: Decimal
    rate: Decimal
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class CashDealData:
    source_ref: str
    client_id: int
    req_id: str
    city: str
    client_name: str
    creator_name: str
    kind: str
    comment: str | None = None
    code: str | None = None
    amount: Decimal | None = None
    in_code: str | None = None
    in_amount: Decimal | None = None
    out_code: str | None = None
    out_amount: Decimal | None = None
    client_text: str | None = None
    request_text: str | None = None
    client_chat_id: int | None = None
    client_message_id: int | None = None


class TelegramDealRegistrarPort(Protocol):
    async def register_exchange(self, data: ExchangeDealData) -> Deal: ...

    async def register_cash(self, data: CashDealData) -> Deal: ...


class TelegramDealRegistrar:
    _RUB_CODES = frozenset({"RUB", "РУБМСК", "РУБСПБ", "РУБПЕР", "РУБТЮМ"})

    def __init__(self, deal_service: DealService, *, default_city: str) -> None:
        self._deal_service = deal_service
        self._default_city = default_city

    async def register_exchange(self, data: ExchangeDealData) -> Deal:
        deal_type = self._exchange_type(data.recv_code, data.pay_code)
        return await self._deal_service.create_source_deal(
            DealCreateCommand(
                deal_type=deal_type,
                city=self._default_city,
                actor_user_id=None,
                client_id=data.client_id,
                source="tg_bot",
                source_kind="exchange",
                source_ref=data.source_ref,
                exchange_client_req_id=data.client_req_id,
                comment=data.comment,
                body={
                    "client_name": data.client_name,
                    "creator_name": data.creator_name,
                    "client_req_id": data.client_req_id,
                    "table_req_id": data.table_req_id,
                    "recv_code": data.recv_code,
                    "recv_amount": str(data.recv_amount),
                    "pay_code": data.pay_code,
                    "pay_amount": str(data.pay_amount),
                    "rate": str(data.rate),
                    "note": data.comment,
                },
            )
        )

    async def register_cash(self, data: CashDealData) -> Deal:
        deal_type = {"dep": "deposit", "wd": "withdrawal", "fx": "conversion"}[data.kind]
        body = {
            "client_name": data.client_name,
            "creator_name": data.creator_name,
            "req_id": data.req_id,
            "request_kind": data.kind,
            "telegram_client_text": data.client_text,
            "telegram_request_text": data.request_text,
            "telegram_client_chat_id": data.client_chat_id,
            "telegram_client_message_id": data.client_message_id,
        }
        if data.kind in {"dep", "wd"}:
            body.update({"currency": data.code, "amount": str(data.amount)})
        else:
            body.update(
                {
                    "in_code": data.in_code,
                    "in_amount": str(data.in_amount),
                    "out_code": data.out_code,
                    "out_amount": str(data.out_amount),
                }
            )
        return await self._deal_service.create_source_deal(
            DealCreateCommand(
                deal_type=deal_type,
                city=data.city,
                actor_user_id=None,
                client_id=data.client_id,
                source="tg_bot",
                source_kind="cash",
                source_ref=data.source_ref,
                comment=data.comment,
                body=body,
            )
        )

    @classmethod
    def _exchange_type(cls, recv_code: str, pay_code: str) -> str:
        recv_is_rub = recv_code.upper() in cls._RUB_CODES
        pay_is_rub = pay_code.upper() in cls._RUB_CODES
        if recv_is_rub and not pay_is_rub:
            return "purchase"
        if pay_is_rub and not recv_is_rub:
            return "sale"
        return "conversion"
