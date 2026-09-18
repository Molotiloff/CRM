from __future__ import annotations

import re
from collections.abc import Iterable
from typing import cast

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.ports.administration import ManagerRepositoryPort
from domain.tron_wallet import is_probable_tron_wallet, normalize_wallet
from services.aml import AMLQueueFullError, AMLQueueService, AMLQueueTask
from telegram_adapters.auth import manager_or_admin_message_required

_RE_AML = re.compile(r"^/амл(?:@\w+)?\s+(\S+)\s*$", re.IGNORECASE)


class AMLHandler:
    def __init__(
        self,
        repo: ManagerRepositoryPort,
        *,
        aml_queue_service: AMLQueueService,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.aml_queue_service = aml_queue_service
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()
        self._register()

    @manager_or_admin_message_required
    async def _cmd_aml(self, message: Message) -> None:
        m = _RE_AML.match((message.text or "").strip())
        if not m:
            await message.answer("Формат: /амл <адрес>")
            return

        wallet = normalize_wallet(m.group(1))
        if not wallet:
            await message.answer("Укажите адрес кошелька.")
            return

        if not is_probable_tron_wallet(wallet):
            await message.answer("Похоже, это не TRON USDT-адрес.")
            return

        wait_msg = await message.answer("⏳ AML-проверка добавлена в очередь...")
        bot = cast(Bot, message.bot)
        chat_id = message.chat.id
        wait_message_id = wait_msg.message_id

        async def on_success(result: dict) -> None:
            try:
                await bot.edit_message_text(
                    result["message_text"],
                    chat_id=chat_id,
                    message_id=wait_message_id,
                )
            except TelegramAPIError:
                await bot.send_message(chat_id, result["message_text"])

        async def on_error(exc: Exception) -> None:
            await bot.edit_message_text(
                f"❌ AML-проверка завершилась ошибкой:\n<code>{exc}</code>",
                chat_id=chat_id,
                message_id=wait_message_id,
                parse_mode="HTML",
            )

        try:
            position = await self.aml_queue_service.enqueue(
                AMLQueueTask(
                    wallet=wallet,
                    on_success=on_success,
                    on_error=on_error,
                )
            )
        except AMLQueueFullError:
            await wait_msg.edit_text(
                "❌ Очередь AML-проверок заполнена. Попробуйте повторить команду позже."
            )
            return

        if position == 1:
            await wait_msg.edit_text("⏳ AML-проверка поставлена в обработку...")
        else:
            await wait_msg.edit_text(
                f"⏳ AML-проверка добавлена в очередь.\n"
                f"Позиция в очереди: <code>{position}</code>",
                parse_mode="HTML",
            )

    def _register(self) -> None:
        self.router.message.register(self._cmd_aml, Command("амл"))
