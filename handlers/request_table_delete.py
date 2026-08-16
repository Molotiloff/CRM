from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from keyboards.request import CB_TABLE_DEL_NO, CB_TABLE_DEL_YES
from services.request_table.delete_interaction_service import RequestTableDeleteInteractionService
from telegram_adapters import (
    AiogramCallbackReplier,
    AiogramMessenger,
    request_table_callback_command,
)


def get_table_delete_router(
    *, interaction_service: RequestTableDeleteInteractionService
) -> Router:
    router = Router()

    async def handle_no(callback: CallbackQuery) -> None:
        command = request_table_callback_command(callback)
        if command is None:
            await callback.answer("Сообщение недоступно.", show_alert=True)
            return
        await interaction_service.handle_no(
            command,
            messenger=AiogramMessenger(callback.bot),
            replier=AiogramCallbackReplier(callback),
        )

    async def handle_yes(callback: CallbackQuery) -> None:
        command = request_table_callback_command(callback)
        if command is None:
            await callback.answer("Сообщение недоступно.", show_alert=True)
            return
        await interaction_service.handle_yes(
            command,
            messenger=AiogramMessenger(callback.bot),
            replier=AiogramCallbackReplier(callback),
        )

    router.callback_query.register(
        handle_no,
        F.data.startswith(CB_TABLE_DEL_NO),
    )
    router.callback_query.register(
        handle_yes,
        F.data.startswith(CB_TABLE_DEL_YES),
    )
    return router
