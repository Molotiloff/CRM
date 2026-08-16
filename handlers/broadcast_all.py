from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from db_asyncpg.ports.administration import ManagerRepositoryPort
from services.broadcast import BroadcastCommand, BroadcastService
from telegram_adapters import (
    AiogramBroadcastDelivery,
    AiogramBroadcastPresenter,
    AiogramBroadcastSessionStore,
)
from telegram_adapters.auth import (
    require_manager_or_admin_callback,
    require_manager_or_admin_message,
)
from telegram_adapters.errors import suppress_telegram_edit_errors


class BroadcastAllHandler:
    CB_CONFIRM = AiogramBroadcastPresenter.CB_CONFIRM
    CB_CANCEL = AiogramBroadcastPresenter.CB_CANCEL

    def __init__(
        self,
        repo: ManagerRepositoryPort,
        *,
        broadcast_service: BroadcastService,
        session_store: AiogramBroadcastSessionStore,
        preview_builder: AiogramBroadcastPresenter,
        admin_chat_ids: set[int] | None = None,
        admin_user_ids: set[int] | None = None,
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()
        self.session_store = session_store
        self.broadcast_service = broadcast_service
        self.preview_builder = preview_builder
        self._register()

    async def _cmd_all(self, message: Message) -> None:
        if message.chat.id not in self.admin_chat_ids:
            await message.answer("Команда /всем доступна только в админском чате.")
            return

        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        target_group = self.broadcast_service.extract_group_from_command(message.text or "")
        clients = await self.broadcast_service.get_target_clients(group=target_group)

        if not clients:
            await message.answer(self.broadcast_service.empty_clients_text(target_group))
            return

        prompt = await message.answer(
            f"{self.preview_builder.BROADCAST_PROMPT_TEXT}\n\n"
            f"Целевая аудитория: {self.broadcast_service.target_label(target_group)}."
        )
        self.session_store.add_prompt(
            chat_id=message.chat.id,
            prompt_message_id=prompt.message_id,
            group=target_group,
        )

    async def _handle_broadcast_reply(self, message: Message) -> None:
        reply_msg = message.reply_to_message
        if not reply_msg:
            return

        if not self.session_store.is_pending_prompt(
            chat_id=message.chat.id,
            prompt_message_id=reply_msg.message_id,
        ):
            return

        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        if message.media_group_id:
            await self._collect_and_preview_media_group(message)
            return

        await self._preview_and_confirm(message)
        self.session_store.remove_prompt(
            chat_id=message.chat.id,
            prompt_message_id=reply_msg.message_id,
        )

    async def _collect_and_preview_media_group(self, message: Message) -> None:
        key = self.session_store.add_media_group_message(
            chat_id=message.chat.id,
            media_group_id=message.media_group_id,
            message=message,
        )

        await asyncio.sleep(1.0)

        if not self.session_store.has_media_group(key):
            return

        messages = self.session_store.pop_media_group(key)
        if not messages:
            return

        reply_msg = messages[0].reply_to_message
        if not reply_msg:
            return

        if not self.session_store.is_pending_prompt(
            chat_id=messages[0].chat.id,
            prompt_message_id=reply_msg.message_id,
        ):
            return

        await self._preview_and_confirm_media_group(messages)
        self.session_store.remove_prompt(
            chat_id=messages[0].chat.id,
            prompt_message_id=reply_msg.message_id,
        )

    async def _preview_and_confirm(self, source_message: Message) -> None:
        reply_msg = source_message.reply_to_message
        target_group = self.session_store.prompt_group(
            prompt_message_id=reply_msg.message_id if reply_msg else None,
        )

        preview = await self.preview_builder.preview_single(
            source_message=source_message,
            target_group=target_group,
        )
        if not preview:
            return

        control_message_id, payload = preview
        self.session_store.add_payload(
            control_message_id=control_message_id,
            payload=payload,
        )

    async def _preview_and_confirm_media_group(self, messages: list[Message]) -> None:
        reply_msg = messages[0].reply_to_message
        target_group = self.session_store.prompt_group(
            prompt_message_id=reply_msg.message_id if reply_msg else None,
        )

        preview = await self.preview_builder.preview_media_group(
            messages=messages,
            target_group=target_group,
            target_label=self.broadcast_service.target_label(target_group),
        )
        if not preview:
            return

        control_message_id, payload = preview
        self.session_store.add_payload(
            control_message_id=control_message_id,
            payload=payload,
        )

    async def _handle_broadcast_action(self, cq: CallbackQuery) -> None:
        if not await require_manager_or_admin_callback(
            self.repo,
            cq,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        msg = cq.message
        if not msg:
            await cq.answer()
            return

        payload = self.session_store.get_payload(control_message_id=msg.message_id)
        if not payload:
            await cq.answer("Данные рассылки устарели.", show_alert=True)
            return

        if cq.data == self.CB_CANCEL:
            self.session_store.pop_payload(control_message_id=msg.message_id)
            with suppress_telegram_edit_errors(context="broadcast cancel"):
                await msg.edit_reply_markup(reply_markup=None)
            await msg.answer("❌ Рассылка отменена.")
            await cq.answer()
            return

        if cq.data != self.CB_CONFIRM:
            await cq.answer()
            return

        payload = self.session_store.pop_payload(control_message_id=msg.message_id)
        if not payload:
            await cq.answer("Данные рассылки устарели.", show_alert=True)
            return

        with suppress_telegram_edit_errors(context="broadcast confirm"):
            await msg.edit_reply_markup(reply_markup=None)

        await cq.answer("Запускаю рассылку...")
        result = await self.broadcast_service.send(
            BroadcastCommand(target_group=payload.group),
            delivery=AiogramBroadcastDelivery(bot=msg.bot, payload=payload),
        )
        await msg.answer(
            self.broadcast_service.result_text(result),
            parse_mode="HTML",
        )

    def _register(self) -> None:
        self.router.message.register(self._cmd_all, Command("всем"))
        self.router.message.register(
            self._handle_broadcast_reply,
            F.reply_to_message.as_("reply_to_message"),
        )
        self.router.callback_query.register(
            self._handle_broadcast_action,
            F.data.in_({self.CB_CONFIRM, self.CB_CANCEL}),
        )
