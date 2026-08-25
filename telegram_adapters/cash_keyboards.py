from aiogram.types import InlineKeyboardMarkup

from keyboards import cash_completion_kb, deal_kb
from services.cash_requests.keyboard_port import CashKeyboardPort


class AiogramCashKeyboardPresenter(CashKeyboardPort):
    def deal_actions(self, *, request_id: str) -> InlineKeyboardMarkup:
        return deal_kb(request_id)

    def completion_action(self, *, request_id: str) -> InlineKeyboardMarkup:
        return cash_completion_kb(request_id)
