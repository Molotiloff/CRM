from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from db_asyncpg.ports.workflows import ManagedClientWalletTransactionRepositoryPort
from domain import DomainStateError, DomainValidationError
from services.accounting.cash_settlement_models import CashSettlementCommand
from services.accounting.cash_settlement_service import (
    CashSettlementError,
    CashSettlementService,
)
from services.wallets import WalletInteractionService
from services.wallets.models import CurrencyChangeCommand
from telegram_adapters import ChatLockRegistry, CityCashMediaStore
from telegram_adapters.auth import (
    manager_or_admin_callback_required,
    manager_or_admin_message_required,
    require_manager_or_admin_message,
)
from telegram_adapters.city_cash_transfer import city_cash_transfer_to_client
from telegram_adapters.errors import suppress_telegram_edit_errors
from telegram_adapters.message_context import get_chat_name
from telegram_adapters.statements import handle_stmt_callback

_RE_PUBLIC_WALLET_CMD = r"(?iu)^/кош(?:@\w+)?(?:\s|$)"
_RE_CASH_REQUEST_ID = re.compile(r"(?iu)^Б-\d{6}$")


class WalletsHandler:
    def __init__(
        self,
        repo: ManagedClientWalletTransactionRepositoryPort,
        interaction_service: WalletInteractionService,
        city_cash_media_store: CityCashMediaStore,
        chat_locks: ChatLockRegistry,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
        *,
        request_chat_id: int | None = None,
        ignore_chat_ids: Iterable[int] | None = None,
        city_cash_chats: Mapping[str, int] | None = None,
        cash_settlement_service: CashSettlementService | None = None,
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.request_chat_id = int(request_chat_id) if request_chat_id is not None else None
        self.ignore_chat_ids = set(ignore_chat_ids or [])
        self.city_cash_chats = dict(city_cash_chats or {})
        self.city_cash_chat_ids = set(self.city_cash_chats.values())
        self.cash_settlement_service = cash_settlement_service
        self._background_tasks: set[asyncio.Task[None]] = set()
        self.city_cash_media_store = city_cash_media_store
        self.chat_locks = chat_locks
        self.interaction_service = interaction_service
        self.router = Router()
        self._register()

    async def _process_buffered_city_cash_command(self, message: Message) -> None:
        await asyncio.sleep(0.8)
        async with self.chat_locks.for_chat(message.chat.id):
            await self._execute_currency_change(message)

    @manager_or_admin_message_required
    async def _cmd_wallet(self, message: Message) -> None:
        result = await self.interaction_service.build_wallet_response(
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
        )
        await message.answer(
            result.message_text,
            parse_mode="HTML",
            reply_markup=result.reply_markup,
        )

    @manager_or_admin_message_required
    async def _cmd_rmcur(self, message: Message) -> None:
        result = await self.interaction_service.build_remove_currency_response(
            raw_text=message.text or "",
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
        )
        await message.answer(result.message_text, reply_markup=result.reply_markup)

    @manager_or_admin_message_required
    async def _cmd_addcur(self, message: Message) -> None:
        result = await self.interaction_service.build_add_currency_response(
            raw_text=message.text or "",
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
        )
        await message.answer(result.message_text)

    async def _on_currency_change(self, message: Message) -> None:
        if message.from_user and message.bot and message.from_user.id == message.bot.id:
            return

        if message.chat and message.chat.id in self.ignore_chat_ids:
            return

        if self.request_chat_id is not None and int(message.chat.id) == self.request_chat_id:
            await message.answer(
                "В заявочном чате команды кошелька вида /usd, /usdt и т.д. недоступны."
            )
            return

        if (
            message.media_group_id
            and message.photo
            and message.chat.id in self.city_cash_chat_ids
        ):
            self.city_cash_media_store.add_message(
                chat_id=message.chat.id,
                media_group_id=message.media_group_id,
                message=message,
            )
            task = asyncio.create_task(
                self._process_buffered_city_cash_command(message),
                name=f"city_cash_album:{message.chat.id}:{message.media_group_id}:{message.message_id}",
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return

        text = message.text or message.caption or ""
        reply = getattr(message, "reply_to_message", None)
        if (
            reply
            and message.bot
            and reply.from_user
            and reply.from_user.id == message.bot.id
            and re.match(_RE_PUBLIC_WALLET_CMD, text.strip())
        ):
            return

        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        async with self.chat_locks.for_chat(message.chat.id):
            await self._execute_currency_change(message)

    async def _execute_currency_change(self, message: Message) -> None:
        raw_text = message.text or message.caption or ""
        try:
            parsed = self.interaction_service.wallet_service.parse_currency_change(
                raw_text,
                chat_id=message.chat.id,
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        if parsed is None:
            return
        request_id = parsed.client_name_for_transfer.strip()
        if (
            parsed.is_city_cash
            and self.cash_settlement_service is not None
            and request_id
            and not _RE_CASH_REQUEST_ID.fullmatch(request_id)
        ):
            await message.answer(
                "Для движения клиентской наличности укажите номер заявки, "
                "например: /usd -125 Б-123456. Связь по имени отключена."
            )
            return
        if (
            parsed.is_city_cash
            and self.cash_settlement_service is not None
            and _RE_CASH_REQUEST_ID.fullmatch(request_id)
        ):
            evidence_messages = [message]
            if message.media_group_id:
                grouped = self.city_cash_media_store.pop_group(
                    chat_id=message.chat.id,
                    media_group_id=message.media_group_id,
                )
                if grouped:
                    evidence_messages = grouped
            try:
                settled = await self.cash_settlement_service.settle(
                    CashSettlementCommand(
                        request_id=request_id,
                        city_chat_id=message.chat.id,
                        command_message_id=message.message_id,
                        currency=parsed.code,
                        signed_qty=parsed.amount,
                        actor_tg_user_id=(
                            message.from_user.id if message.from_user is not None else None
                        ),
                        evidence={
                            "telegramChatId": message.chat.id,
                            "telegramMessageId": message.message_id,
                            "mediaGroupId": message.media_group_id,
                            "photoFileIds": [
                                photo.file_id
                                for evidence_message in evidence_messages
                                for photo in (evidence_message.photo or [])[-1:]
                            ],
                            "comment": parsed.extra_comment,
                        },
                    )
                )
            except (CashSettlementError, DomainStateError, DomainValidationError) as exc:
                await message.answer(str(exc))
                return
            suffix = " (повтор)" if settled.repeated else ""
            await message.answer(
                f"✅ Заявка {settled.request_id} проведена: "
                f"{settled.actual_qty} {settled.currency}{suffix}."
            )
            return
        result = await self.interaction_service.build_currency_change_response(
            CurrencyChangeCommand(
                chat_id=message.chat.id,
                chat_name=get_chat_name(message),
                message_id=message.message_id,
                parsed=parsed,
            )
        )
        text = result.message_text
        if result.ok and parsed.is_city_cash and parsed.client_name_for_transfer:
            transfer = await city_cash_transfer_to_client(
                repo=self.repo,
                bot=message.bot,
                src_message=message,
                media_store=self.city_cash_media_store,
                currency_code=parsed.code,
                amount_signed=parsed.amount,
                amount_expr=parsed.expr,
                client_name_exact=parsed.client_name_for_transfer,
                extra_comment=parsed.extra_comment,
            )
            if transfer.ok:
                text += "\n✅ Транзакция проведена в чате у клиента!"
            else:
                text += (
                    "\n⚠️ "
                    + (transfer.error or "Не удалось продублировать операцию в чат клиента.")
                )
        await message.answer(text, reply_markup=result.reply_markup)

    async def _buffer_city_cash_media_group(self, message: Message) -> None:
        if not message.media_group_id or not message.photo:
            return
        if message.chat.id not in self.city_cash_chat_ids:
            return
        self.city_cash_media_store.add_message(
            chat_id=message.chat.id,
            media_group_id=message.media_group_id,
            message=message,
        )

    @manager_or_admin_callback_required
    async def _cb_rmcur(self, cq: CallbackQuery) -> None:
        try:
            code_raw, answer = self.interaction_service.parse_remove_currency_callback(cq.data)
        except ValueError:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        if not cq.message:
            await cq.answer("Нет чата", show_alert=True)
            return

        edit_text, answer_text, show_alert = await self.interaction_service.build_remove_currency_callback_response(
            chat_id=cq.message.chat.id,
            chat_name=get_chat_name(cq.message),
            code_raw=code_raw,
            answer=answer,
        )
        await cq.message.edit_text(edit_text)
        await cq.answer(answer_text, show_alert=show_alert)

    @manager_or_admin_callback_required
    async def _cb_undo(self, cq: CallbackQuery) -> None:
        try:
            parsed_undo = self.interaction_service.parse_undo_callback(cq.data)
            if not parsed_undo:
                return
            code_raw, sign, amt_str = parsed_undo
        except ValueError:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        if not cq.message:
            await cq.answer("Нет сообщения", show_alert=True)
            return

        async with self.chat_locks.for_chat(cq.message.chat.id):
            result = await self.interaction_service.build_undo_response(
                chat_id=cq.message.chat.id,
                chat_name=get_chat_name(cq.message),
                message_id=cq.message.message_id,
                code_raw=code_raw,
                sign=sign,
                amt_str=amt_str,
            )

            if result.ok:
                old_text = cq.message.text or ""
                with suppress_telegram_edit_errors(context="wallet undo"):
                    await cq.message.edit_text(old_text + "\n↩️ Отменено.")

            with suppress_telegram_edit_errors(context="wallet undo"):
                await cq.message.edit_reply_markup(reply_markup=None)

            await cq.message.answer(result.message_text, parse_mode="HTML")
            await cq.answer("Откат выполнен" if result.ok else result.message_text[:100], show_alert=not result.ok)

    async def _cb_statement(self, cq: CallbackQuery) -> None:
        await handle_stmt_callback(cq, self.repo)

    def _register(self) -> None:
        self.router.message.register(self._cmd_wallet, Command("кошелек"))
        self.router.message.register(self._cmd_addcur, Command("добавь"))
        self.router.message.register(self._cmd_rmcur, Command("удали"))

        self.router.message.register(
            self._on_currency_change,
            F.text.regexp(r"^/[A-Za-zА-Яа-я0-9_]+\s+"),
        )
        self.router.message.register(
            self._on_currency_change,
            F.caption.regexp(r"^/[A-Za-zА-Яа-я0-9_]+\s+"),
        )
        self.router.message.register(
            self._buffer_city_cash_media_group,
            F.media_group_id,
        )

        self.router.callback_query.register(self._cb_rmcur, F.data.startswith("rmcur:"))
        self.router.callback_query.register(self._cb_undo, F.data.startswith("undo:"))
        self.router.callback_query.register(self._cb_statement, F.data.in_({"stmt:month", "stmt:all"}))
