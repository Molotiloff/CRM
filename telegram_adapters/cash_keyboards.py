from aiogram.types import InlineKeyboardMarkup

from keyboards import deal_kb
from services.cash_requests.keyboard_port import CashKeyboardPort


class AiogramCashKeyboardPresenter(CashKeyboardPort):
    def deal_actions(self, *, request_id: str) -> InlineKeyboardMarkup:
        return deal_kb(request_id)
