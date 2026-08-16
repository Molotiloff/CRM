from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class AiogramPaymentWatchPresenter:
    @staticmethod
    def stop_keyboard(watch_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Отменить ожидание",
                        callback_data=f"paywatch:stop:{watch_id}",
                    ),
                ]
            ]
        )

    @staticmethod
    def timeout_keyboard(watch_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Продолжить",
                        callback_data=f"paywatch:continue:{watch_id}",
                    ),
                    InlineKeyboardButton(
                        text="Остановить",
                        callback_data=f"paywatch:stop:{watch_id}",
                    ),
                ]
            ]
        )

