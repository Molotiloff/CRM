import logging
from collections.abc import Iterable

import asyncpg
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from keyboards import confirm_kb
from services.admin_client import ClientDirectoryService
from telegram_adapters.errors import suppress_telegram_edit_errors

log = logging.getLogger(__name__)


class ClientsHandler:
    """
    /клиенты — список активных клиентов
    /клиенты <группа> — список активных клиентов указанной группы
    /rmclient <chat_id> — мягко удалить клиента (is_active=false) с подтверждением
    """

    def __init__(self, service: ClientDirectoryService, admin_chat_ids: Iterable[int] | None = None) -> None:
        self.service = service
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.router = Router()
        self._register()

    async def _cmd_clients(self, message: Message) -> None:
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Команда доступна только в админском чате.")
            return

        parts = (message.text or "").split(maxsplit=1)
        group = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None

        for chunk in await self.service.build_clients_chunks(group=group):
            await message.answer(chunk, parse_mode="HTML")

    async def _cmd_rmclient(self, message: Message) -> None:
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Команда доступна только в админском чате.")
            return

        chat_id_to_remove = self.service.parse_rmclient_chat_id(message.text or "")
        if chat_id_to_remove is None:
            await message.answer("Использование: /rmclient <chat_id>")
            return
        if chat_id_to_remove == -1:
            await message.answer("Ошибка: chat_id должен быть числом.")
            return

        await message.answer(
            self.service.build_remove_confirmation(chat_id_to_remove),
            parse_mode="HTML",
            reply_markup=confirm_kb(
                yes_cb=f"rmcli:{chat_id_to_remove}:yes",
                no_cb=f"rmcli:{chat_id_to_remove}:no",
            ),
        )

    async def _cb_rmclient(self, cq: CallbackQuery) -> None:
        if self.admin_chat_ids and (not cq.message or cq.message.chat.id not in self.admin_chat_ids):
            await cq.answer("Доступно только в админском чате.", show_alert=True)
            return

        try:
            kind, chat_id_str, answer = (cq.data or "").split(":")
            if kind != "rmcli":
                return
            chat_id_to_remove = int(chat_id_str)
        except ValueError:
            await cq.answer("Некорректные данные.", show_alert=True)
            return

        if answer == "no":
            old = cq.message.text or ""
            with suppress_telegram_edit_errors(context="rmclient cancel"):
                await cq.message.edit_text(old + "\nОтменено.", parse_mode="HTML")
            with suppress_telegram_edit_errors(context="rmclient cancel"):
                await cq.message.edit_reply_markup(reply_markup=None)
            await cq.answer("Отмена")
            return

        try:
            new_text = await self.service.confirm_remove(chat_id_to_remove)
        except asyncpg.PostgresError as e:
            log.exception("Failed to remove client %s", chat_id_to_remove)
            await cq.answer(f"Ошибка: {e}", show_alert=True)
            return
        with suppress_telegram_edit_errors(context="rmclient confirm"):
            await cq.message.edit_text(new_text, parse_mode="HTML")
        with suppress_telegram_edit_errors(context="rmclient confirm"):
            await cq.message.edit_reply_markup(reply_markup=None)
        await cq.answer("Готово")

    def _register(self) -> None:
        self.router.message.register(self._cmd_clients, Command("клиенты"))
        self.router.message.register(self._cmd_rmclient, Command("rmclient"))
        self.router.callback_query.register(self._cb_rmclient, F.data.startswith("rmcli:"))
