"""Permanent missing-message responses for Telegram live editors."""

from aiogram.exceptions import TelegramBadRequest, TelegramNotFound


def is_missing_edit_target(exc: TelegramBadRequest | TelegramNotFound) -> bool:
    description = exc.message.lower().strip().removeprefix("bad request:").strip()
    return description.rstrip(".") in {
        "message to edit not found",
        "message not found",
        "message_id_invalid",
        "chat not found",
    }
