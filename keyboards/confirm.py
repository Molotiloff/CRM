# keyboards/confirm.py
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def rmcur_confirm_kb(code: str) -> InlineKeyboardMarkup:
    """
    Подтверждение удаления валюты
    """
    code = code.strip().upper()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Удалить {code}", callback_data=f"rmcur:{code}:yes")],
        [InlineKeyboardButton(text="Отмена", callback_data=f"rmcur:{code}:no")]
    ])


def confirm_kb(
    yes_cb: str,
    no_cb: str,
    *,
    yes_text: str = "✅ Да",
    no_text: str = "✖️ Нет",
) -> InlineKeyboardMarkup:
    """
    Универсальное подтверждение: кнопки «Да / Нет».
    yes_cb, no_cb — callback_data для каждой кнопки.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=yes_text, callback_data=yes_cb)],
        [InlineKeyboardButton(text=no_text, callback_data=no_cb)],
    ])
