# keyboards/main.py
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


class MainKeyboard:
    @staticmethod
    def main() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="/помоги")],
                [KeyboardButton(text="/дай"), KeyboardButton(text="/кош")],
                [KeyboardButton(text="/екб"), KeyboardButton(text="/члб"), KeyboardButton(text="/тюм")],
            ],
            resize_keyboard=True,
            input_field_placeholder="Выберите команду"
        )
