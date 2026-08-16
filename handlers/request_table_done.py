from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from keyboards.request import CB_TABLE_DONE
from services.request_table.done_interaction_service import RequestTableDoneInteractionService
from telegram_adapters import (
    AiogramCallbackReplier,
    AiogramMessenger,
    request_table_callback_command,
)


def get_table_done_router(
    *, interaction_service: RequestTableDoneInteractionService
) -> Router:
    router = Router()

    async def handle(callback: CallbackQuery) -> None:
        command = request_table_callback_command(callback)
        if command is None:
            await callback.answer("Сообщение недоступно.", show_alert=True)
            return
        await interaction_service.handle(
            command,
            messenger=AiogramMessenger(callback.bot),
            replier=AiogramCallbackReplier(callback),
        )

    router.callback_query.register(
        handle,
        F.data.startswith(CB_TABLE_DONE),
    )
    return router
