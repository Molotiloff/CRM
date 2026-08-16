from __future__ import annotations

import re
from collections.abc import Iterable

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.ports.administration import ManagerRepositoryPort
from domain.tron_wallet import is_probable_tron_wallet, normalize_wallet
from services.aml import AMLQueueService, AMLQueueTask
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

        async def on_success(result: dict) -> None:
            try:
                await wait_msg.edit_text(result["message_text"])
            except TelegramAPIError:
                await message.answer(result["message_text"])

        async def on_error(exc: Exception) -> None:
            await wait_msg.edit_text(
                f"❌ AML-проверка завершилась ошибкой:\n<code>{exc}</code>",
                parse_mode="HTML",
            )

        position = await self.aml_queue_service.enqueue(
            AMLQueueTask(
                wallet=wallet,
                on_success=on_success,
                on_error=on_error,
            )
        )

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
