from collections.abc import Iterable

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.admin_client import ClientGroupService


class CityAssignHandler:
    """
    /группа <chat_id> <группа>. Доступ: только из admin_chat_ids.
    """

    def __init__(self, service: ClientGroupService, admin_chat_ids: Iterable[int] | None = None) -> None:
        self.service = service
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.router = Router()
        self._register()

    async def _cmd_group(self, message: Message) -> None:
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Команда доступна только в админском чате.")
            return

        await message.answer(await self.service.assign_from_text(message.text or ""))

    def _register(self) -> None:
        self.router.message.register(self._cmd_group, Command("группа"))
