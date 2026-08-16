from __future__ import annotations

from aiogram.types import CallbackQuery

from services.request_table.models import RequestTableCallbackCommand


def request_table_callback_command(
    callback: CallbackQuery,
) -> RequestTableCallbackCommand | None:
    message = callback.message
    if message is None:
        return None
    return RequestTableCallbackCommand(
        chat_id=message.chat.id,
        message_id=message.message_id,
        message_text=getattr(message, "text", None) or "",
        message_dt=getattr(message, "date", None),
        callback_data=callback.data or "",
    )
