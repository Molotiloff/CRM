from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class AiogramRequestTableKeyboardPresenter:
    @staticmethod
    def processing() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⏳ Обрабатывается…",
                        callback_data="noop",
                    )
                ]
            ]
        )
