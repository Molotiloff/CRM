# handlers/managers.py
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.admin_client import ManagerAdminService


class ManagersHandler:
    """
    /mgr — управление менеджерами.
    Доступ: только в одном админском чате (admin_chat_id). Любой участник этого чата.
    """
    def __init__(self, service: ManagerAdminService, admin_chat_id: int) -> None:
        self.service = service
        self.admin_chat_id = int(admin_chat_id)
        self.router = Router()
        self._register()

    def _allowed(self, m: Message) -> bool:
        return bool(m.chat and m.chat.id == self.admin_chat_id)

    async def _cmd_mgr(self, message: Message) -> None:
        if not self._allowed(message):
            await message.answer("⛔ Команда доступна только в админском чате.")
            return

        await message.answer(await self.service.handle_command(message.text or ""), parse_mode="HTML")

    def _register(self) -> None:
        self.router.message.register(self._cmd_mgr, Command("mgr"))
