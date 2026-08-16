from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from services.payment_watch import (
    PaymentWatchError,
    PaymentWatchService,
    StartPaymentWatchCommand,
)
from telegram_adapters import AiogramPaymentWatchPresenter
from telegram_adapters.auth import (
    manager_or_admin_callback_required,
    manager_or_admin_message_required,
)
from telegram_adapters.message_context import get_chat_name


class PaymentWatchHandler:
    def __init__(
        self,
        *,
        repo,
        payment_watch_service: PaymentWatchService,
        presenter: AiogramPaymentWatchPresenter,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.payment_watch_service = payment_watch_service
        self.presenter = presenter
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()
        self._register()

    @manager_or_admin_message_required
    async def _cmd_payment(self, message: Message) -> None:
        raw = (message.text or "").strip()
        try:
            test_mode, manager_note = self._parse_send_command(raw)
        except PaymentWatchError as exc:
            await message.answer(str(exc))
            return
        reply = message.reply_to_message
        if reply is None:
            await message.answer(
                "Команда должна быть ответом на сообщение с кошельком клиента."
            )
            return
        try:
            started = await self.payment_watch_service.start_watch(
                StartPaymentWatchCommand(
                    chat_id=message.chat.id,
                    chat_name=get_chat_name(message),
                    reply_message_id=reply.message_id,
                    reply_text=reply.text,
                    reply_caption=reply.caption,
                    created_by_user_id=(message.from_user.id if message.from_user else None),
                    test_mode=test_mode,
                    manager_note=manager_note,
                )
            )
        except PaymentWatchError as exc:
            await message.answer(str(exc))
            return
        sent = await message.answer(
            started.message_text,
            parse_mode="HTML",
            reply_markup=self.presenter.stop_keyboard(started.watch_id),
        )
        await self.payment_watch_service.set_notice_message_id(
            watch_id=started.watch_id,
            message_id=sent.message_id,
        )

    @manager_or_admin_callback_required
    async def _cb_continue(self, cq: CallbackQuery) -> None:
        data = cq.data or ""
        try:
            watch_id = int(data.rsplit(":", 1)[-1])
            text = await self.payment_watch_service.continue_watch(watch_id=watch_id)
        except (ValueError, PaymentWatchError) as exc:
            await cq.answer(str(exc), show_alert=True)
            return
        await cq.answer("Ожидание продлено")
        if cq.message:
            await cq.message.edit_text(text, parse_mode="HTML")

    @manager_or_admin_callback_required
    async def _cb_stop(self, cq: CallbackQuery) -> None:
        data = cq.data or ""
        try:
            watch_id = int(data.rsplit(":", 1)[-1])
            text = await self.payment_watch_service.stop_watch(watch_id=watch_id)
        except (ValueError, PaymentWatchError) as exc:
            await cq.answer(str(exc), show_alert=True)
            return
        await cq.answer("Ожидание остановлено")
        if cq.message:
            await cq.message.edit_text(text, parse_mode="HTML")

    def _register(self) -> None:
        self.router.message.register(self._cmd_payment, Command("отпр"))
        self.router.callback_query.register(self._cb_continue, F.data.startswith("paywatch:continue:"))
        self.router.callback_query.register(self._cb_stop, F.data.startswith("paywatch:stop:"))

    @staticmethod
    def _parse_send_command(raw: str) -> tuple[bool, str | None]:
        parts = raw.split()
        args = parts[1:]
        if not args:
            return False, None

        first = args[0].strip().lower()
        test_mode = first in {"тест", "test"}
        note_token: str | None = None

        if test_mode:
            if len(args) >= 2:
                note_token = args[1].strip()
            if len(args) > 2:
                raise PaymentWatchError("Используйте: /отпр, /отпр 10000, /отпр тест, /отпр тест 10000")
        else:
            note_token = args[0].strip()
            if len(args) > 1:
                raise PaymentWatchError("Используйте: /отпр, /отпр 10000, /отпр тест, /отпр тест 10000")

        if note_token:
            try:
                Decimal(note_token.replace(",", "."))
            except (InvalidOperation, ValueError):
                raise PaymentWatchError("Комментарий-сумма должен быть числом. Примеры: /отпр 10000, /отпр тест 10000") from None
            return test_mode, note_token

        return test_mode, None


__all__ = ["PaymentWatchHandler"]
