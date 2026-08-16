from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from keyboards import rmcur_confirm_kb
from services.wallets.keyboard_port import WalletKeyboardPort
from telegram_adapters.statements import statements_kb


class AiogramWalletKeyboardPresenter(WalletKeyboardPort):
    def statements(self) -> InlineKeyboardMarkup:
        return statements_kb()

    def remove_currency(self, *, code: str) -> InlineKeyboardMarkup:
        return rmcur_confirm_kb(code)

    def undo(self, *, code: str, sign: str, amount: str) -> InlineKeyboardMarkup:
        callback_data = f"undo:{code.upper()}:{sign}:{amount}"
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Откатить изменение",
                        callback_data=callback_data,
                    )
                ]
            ]
        )
