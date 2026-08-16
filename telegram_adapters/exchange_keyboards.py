from __future__ import annotations

from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from keyboards import delete_from_table_keyboard, request_keyboard
from services.exchange.keyboard_port import ExchangeKeyboardPort


class AiogramExchangeKeyboardPresenter(ExchangeKeyboardPort):
    def client_cancel(
        self,
        *,
        request_id: int | str,
        table_request_id: int | str | None,
    ) -> InlineKeyboardMarkup:
        callback_data = f"req_cancel:{request_id}"
        if table_request_id is not None:
            callback_data = f"{callback_data}:{table_request_id}"
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Отменить заявку",
                        callback_data=callback_data,
                    )
                ]
            ]
        )

    def request_chat(
        self,
        *,
        request_id: int | str,
        table_request_id: int | str,
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Занести в таблицу",
                        callback_data=f"req:table_done:{table_request_id}",
                    ),
                    InlineKeyboardButton(
                        text="Отменить заявку",
                        callback_data=f"req_cancel:{request_id}:{table_request_id}",
                    ),
                ]
            ]
        )

    def table_create(
        self,
        *,
        receive_code: str,
        pay_code: str,
        receive_amount: Decimal,
        pay_amount: Decimal,
        rate: Decimal | str,
        table_request_id: int | str,
    ) -> InlineKeyboardMarkup:
        return request_keyboard(
            in_ccy=receive_code,
            out_ccy=pay_code,
            in_amount=receive_amount,
            out_amount=pay_amount,
            client_rate=rate,
            req_id=table_request_id,
        )

    def table_delete(self, *, table_request_id: int | str) -> InlineKeyboardMarkup:
        return delete_from_table_keyboard(req_id=table_request_id)
