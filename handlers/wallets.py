from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from db_asyncpg.ports.workflows import ManagedClientWalletTransactionRepositoryPort
from domain import DomainStateError, DomainValidationError
from services.accounting.cash_settlement_models import CashSettlementCommand
from services.accounting.cash_settlement_service import (
    CashSettlementError,
    CashSettlementService,
)
from services.crm.client_transfer_service import (
    ClientTransferAdjustmentCommand,
    ClientTransferAdjustmentResult,
    ClientTransferCommand,
    ClientTransferResult,
    ClientTransferService,
)
from services.number_formatting import format_amount_core
from services.receipts import ReceiptImageBuilder, ReceiptRow
from services.receipts.image import format_receipt_datetime
from services.wallets import WalletInteractionService
from services.wallets.command_parser import WalletCommandParser
from services.wallets.models import CurrencyChangeCommand
from telegram_adapters import ChatLockRegistry, CityCashMediaStore
from telegram_adapters.auth import (
    manager_or_admin_callback_required,
    manager_or_admin_message_required,
    require_manager_or_admin_message,
)
from telegram_adapters.city_cash_transfer import (
    city_cash_transfer_to_client,
    send_cash_settlement_balance_to_client,
    send_cash_settlement_evidence_to_client,
)
from telegram_adapters.errors import suppress_telegram_edit_errors
from telegram_adapters.message_context import get_chat_name
from telegram_adapters.statements import handle_stmt_callback

_RE_PUBLIC_WALLET_CMD = r"(?iu)^/кош(?:@\w+)?(?:\s|$)"
_RE_CASH_REQUEST_ID = re.compile(r"(?iu)^Б-\d+$")
_TRANSFER_CURRENCIES = frozenset({"RUB", "USDT", "USD", "USDW", "EUR", "EUR500", "THB"})
_TRANSFER_RECEIPT_CAPTION = re.compile(r"^Перевод #(\d+)$")
log = logging.getLogger("wallets")


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
        ignore_chat_ids: Iterable[int] | None = None,
        silent_chat_ids: Iterable[int] | None = None,
        city_cash_chats: Mapping[str, int] | None = None,
        cash_settlement_service: CashSettlementService | None = None,
        client_transfer_service: ClientTransferService | None = None,
        default_city: str = "екб",
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.ignore_chat_ids = set(ignore_chat_ids or [])
        self.silent_chat_ids = set(silent_chat_ids or [])
        self.city_cash_chats = dict(city_cash_chats or {})
        self.city_cash_chat_ids = set(self.city_cash_chats.values())
        self.cash_settlement_service = cash_settlement_service
        self.client_transfer_service = client_transfer_service
        self.receipt_builder = ReceiptImageBuilder()
        self.default_city = default_city
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

    @manager_or_admin_message_required
    async def _cmd_client_transfer(self, message: Message) -> None:
        if self.client_transfer_service is None:
            await message.answer("Переводы временно недоступны")
            return
        if message.chat.id in self.city_cash_chat_ids | self.silent_chat_ids | self.admin_chat_ids:
            await message.answer("Перевод доступен только из клиентского чата")
            return
        receipt_deal_id = self._replied_transfer_id(message)
        if receipt_deal_id is not None:
            parts = (message.text or "").strip().split()
            if len(parts) != 2:
                await message.answer("Для изменения ответьте на чек: /перевод <новая сумма>")
                return
            try:
                new_amount = Decimal(parts[1].replace(",", "."))
                if not new_amount.is_finite() or new_amount <= 0:
                    raise InvalidOperation
            except InvalidOperation:
                await message.answer("Некорректная сумма перевода")
                return
            await self._execute_transfer_adjustment(message, receipt_deal_id, new_amount)
            return
        try:
            amount, currency, recipient_name = self._parse_transfer(message.text or "")
            self._transfer_callback("pick", message.message_id, 1, currency, amount)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        recipients = await self.client_transfer_service.find_recipients(recipient_name)
        if not recipients:
            await message.answer(f"Клиентский чат «{recipient_name}» не найден")
            return
        if len(recipients) > 1:
            buttons = [
                [InlineKeyboardButton(
                    text=f"{item['name']} · {item['chat_id']}",
                    callback_data=self._transfer_callback(
                        "pick", message.message_id, int(item["id"]), currency, amount
                    ),
                )]
                for item in recipients
            ]
            await message.answer(
                "Есть несколько чатов с таким названием. Выберите получателя:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            )
            return
        await self._execute_client_transfer(
            message,
            source_message_id=message.message_id,
            recipient_id=int(recipients[0]["id"]),
            amount=amount,
            currency=currency,
            actor_tg_user_id=message.from_user.id if message.from_user else None,
        )

    @staticmethod
    def _replied_transfer_id(message: Message) -> int | None:
        reply = message.reply_to_message
        if not reply or not reply.photo or not reply.from_user or not message.bot:
            return None
        if reply.from_user.id != message.bot.id:
            return None
        match = _TRANSFER_RECEIPT_CAPTION.fullmatch((reply.caption or "").strip())
        return int(match.group(1)) if match else None

    @manager_or_admin_message_required
    async def _cmd_cancel_transfer(self, message: Message) -> None:
        if self.client_transfer_service is None:
            await message.answer("Переводы временно недоступны")
            return
        deal_id = self._replied_transfer_id(message)
        if deal_id is None:
            await message.answer("Ответьте командой /отмена на чек перевода в чате отправителя")
            return
        await self._execute_transfer_adjustment(message, deal_id, None)

    async def _execute_transfer_adjustment(
        self,
        message: Message,
        deal_id: int,
        new_amount: Decimal | None,
        *,
        source_message_id: int | None = None,
        allow_negative: bool = False,
        actor_tg_user_id: int | None = None,
    ) -> None:
        assert self.client_transfer_service is not None
        source_message_id = source_message_id or message.message_id
        try:
            result = await self.client_transfer_service.adjust(ClientTransferAdjustmentCommand(
                deal_id=deal_id,
                from_chat_id=message.chat.id,
                source_ref=f"{message.chat.id}:{source_message_id}",
                new_amount=new_amount,
                actor_tg_user_id=actor_tg_user_id or (message.from_user.id if message.from_user else None),
                allow_negative=allow_negative,
            ))
        except DomainStateError as exc:
            if "Недостаточно средств" not in str(exc) or allow_negative:
                await message.answer(str(exc))
                return
            raw_amount = "cancel" if new_amount is None else str(new_amount)
            data = f"cta:{deal_id}:{source_message_id}:{raw_amount}"
            if len(data.encode("utf-8")) > 64:
                await message.answer("Сумма слишком длинная для подтверждения в Telegram")
                return
            await message.answer(
                "⚠️ После корректировки баланс одного из клиентов станет отрицательным. "
                "Подтвердить операцию?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Подтвердить", callback_data=data),
                    InlineKeyboardButton(text="Отклонить", callback_data="cta:reject"),
                ]]),
            )
            return
        except DomainValidationError as exc:
            await message.answer(str(exc))
            return
        await self._report_transfer_adjustment(message, result)

    async def _report_transfer_adjustment(
        self, message: Message, result: ClientTransferAdjustmentResult
    ) -> None:
        if result.repeated:
            await message.answer("Эта корректировка уже применена или сумма не изменилась")
            return
        status = "отменён" if result.canceled else "изменён"
        amount = format_amount_core(result.new_amount, result.precision)
        if not result.canceled and message.bot is not None:
            try:
                receipt = self.receipt_builder.build(
                    amount=result.new_amount,
                    currency=result.currency,
                    precision=result.precision,
                    rows=(
                        ReceiptRow("Статус", "Изменено", accent=True),
                        ReceiptRow("Номер операции", f"#{result.deal_id}"),
                        ReceiptRow("Отправитель", result.from_client_name),
                        ReceiptRow("Получатель", result.to_client_name),
                        ReceiptRow("Дата и время", format_receipt_datetime(message.date)),
                    ),
                )
                for chat_id in (message.chat.id, result.to_chat_id):
                    await message.bot.send_photo(
                        chat_id,
                        BufferedInputFile(receipt, filename=f"client_transfer_{result.deal_id}_updated.png"),
                        caption=f"Перевод #{result.deal_id}",
                    )
            except Exception:
                log.exception("Failed to send corrected transfer receipt deal_id=%s", result.deal_id)
        await message.answer(
            f"Перевод #{result.deal_id} {status}.\n"
            f"Сумма: {amount} {result.currency}\n"
            f"Баланс: {format_amount_core(result.from_balance, result.precision)} {result.currency}"
        )
        if message.bot is not None:
            try:
                await message.bot.send_message(
                    result.to_chat_id,
                    f"Перевод #{result.deal_id} {status}.\n"
                    f"Сумма: {amount} {result.currency}\n"
                    f"Баланс: {format_amount_core(result.to_balance, result.precision)} {result.currency}",
                )
            except Exception:
                log.exception("Failed to notify transfer adjustment deal_id=%s", result.deal_id)

    @staticmethod
    def _parse_transfer(raw_text: str) -> tuple[Decimal, str, str]:
        parts = raw_text.strip().split(maxsplit=2)
        if len(parts) < 3:
            raise ValueError(
                "Использование: /перевод 1000 Имя чата или /перевод 100 USDT Имя чата"
            )
        try:
            amount = Decimal(parts[1].replace(",", "."))
        except InvalidOperation:
            raise ValueError("Некорректная сумма перевода") from None
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Сумма перевода должна быть больше нуля")
        tail = parts[2].strip()
        first, _, remainder = tail.partition(" ")
        normalized = WalletCommandParser.normalize_code_alias(first)
        if normalized in _TRANSFER_CURRENCIES and remainder.strip():
            return amount, normalized, remainder.strip()
        return amount, "RUB", tail

    @staticmethod
    def _transfer_callback(
        phase: str, message_id: int, recipient_id: int, currency: str, amount: Decimal
    ) -> str:
        data = f"ct:{phase}:{message_id}:{recipient_id}:{currency}:{amount}"
        if len(data.encode("utf-8")) > 64:
            raise ValueError("Сумма слишком длинная для подтверждения в Telegram")
        return data

    async def _execute_client_transfer(
        self,
        message: Message,
        *,
        source_message_id: int,
        recipient_id: int,
        amount: Decimal,
        currency: str,
        allow_negative: bool = False,
        actor_tg_user_id: int | None = None,
    ) -> None:
        assert self.client_transfer_service is not None
        try:
            result = await self.client_transfer_service.transfer(
                ClientTransferCommand(
                    amount=amount,
                    currency=currency,
                    source="tg_bot",
                    source_ref=f"{message.chat.id}:{source_message_id}",
                    city=self.default_city,
                    from_chat_id=message.chat.id,
                    to_client_id=recipient_id,
                    allow_negative=allow_negative,
                    actor_tg_user_id=actor_tg_user_id,
                )
            )
        except DomainStateError as exc:
            if "Недостаточно средств" not in str(exc) or allow_negative:
                await message.answer(str(exc))
                return
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Подтвердить перевод",
                        callback_data=self._transfer_callback(
                            "confirm", source_message_id, recipient_id, currency, amount
                        ),
                    ),
                    InlineKeyboardButton(
                        text="Отклонить",
                        callback_data=self._transfer_callback(
                            "reject", source_message_id, recipient_id, currency, amount
                        ),
                    ),
                ]
            ])
            await message.answer(
                f"⚠️ Недостаточно {currency} на счёте отправителя. "
                "После подтверждения баланс станет отрицательным. Провести перевод?",
                reply_markup=keyboard,
            )
            return
        except DomainValidationError as exc:
            await message.answer(str(exc))
            return
        await self._report_client_transfer(message, result)

    async def _report_client_transfer(
        self, message: Message, result: ClientTransferResult
    ) -> None:
        suffix = " (повтор)" if result.repeated else ""
        if not result.repeated and message.bot is not None:
            try:
                receipt = self.receipt_builder.build(
                    amount=result.amount,
                    currency=result.currency,
                    precision=result.precision,
                    rows=(
                        ReceiptRow("Статус", "Проведено", accent=True),
                        ReceiptRow("Номер операции", f"#{result.deal_id}"),
                        ReceiptRow("Отправитель", result.from_client_name),
                        ReceiptRow("Получатель", result.to_client_name),
                        ReceiptRow("Дата и время", format_receipt_datetime(result.created_at)),
                    ),
                )
            except Exception:
                log.exception("Failed to build client transfer receipt deal_id=%s", result.deal_id)
            else:
                for chat_id in (message.chat.id, result.to_chat_id):
                    try:
                        await message.bot.send_photo(
                            chat_id,
                            BufferedInputFile(receipt, filename=f"client_transfer_{result.deal_id}.png"),
                            caption=f"Перевод #{result.deal_id}",
                        )
                    except Exception:
                        log.exception(
                            "Failed to send client transfer receipt deal_id=%s chat_id=%s",
                            result.deal_id, chat_id,
                        )
        await message.answer(
            f"Перевод #{result.deal_id} проведён{suffix}.\n"
            f"Получатель: {result.to_client_name}\n"
            f"Списано: {format_amount_core(result.amount, result.precision)} {result.currency}\n"
            f"Баланс: {format_amount_core(result.from_balance, result.precision)} {result.currency}"
        )
        if not result.repeated and message.bot is not None:
            try:
                await message.bot.send_message(
                    result.to_chat_id,
                    f"Перевод #{result.deal_id} от {result.from_client_name}.\n"
                    f"Зачислено: {format_amount_core(result.amount, result.precision)} {result.currency}\n"
                    f"Баланс: {format_amount_core(result.to_balance, result.precision)} {result.currency}",
                )
            except Exception:
                log.exception(
                    "Failed to notify recipient of client transfer deal_id=%s chat_id=%s",
                    result.deal_id, result.to_chat_id,
                )

    @manager_or_admin_callback_required
    async def _cb_client_transfer(self, cq: CallbackQuery) -> None:
        if not isinstance(cq.message, Message) or self.client_transfer_service is None:
            await cq.answer("Сообщение недоступно", show_alert=True)
            return
        try:
            _, phase, source_message_id, recipient_id, currency, raw_amount = (
                cq.data or ""
            ).split(":", maxsplit=5)
            amount = Decimal(raw_amount)
            parsed_source_message_id = int(source_message_id)
            parsed_recipient_id = int(recipient_id)
            if phase not in {"pick", "confirm", "reject"} or currency not in _TRANSFER_CURRENCIES:
                raise ValueError
        except (ValueError, InvalidOperation):
            await cq.answer("Некорректное подтверждение", show_alert=True)
            return
        if phase == "reject":
            try:
                rejected = await self.client_transfer_service.reject(
                    ClientTransferCommand(
                        amount=amount,
                        currency=currency,
                        source="tg_bot",
                        source_ref=f"{cq.message.chat.id}:{parsed_source_message_id}",
                        city=self.default_city,
                        from_chat_id=cq.message.chat.id,
                        to_client_id=parsed_recipient_id,
                        actor_tg_user_id=cq.from_user.id,
                    )
                )
            except (DomainStateError, DomainValidationError) as exc:
                await cq.answer(str(exc), show_alert=True)
                return
            await cq.message.edit_text(
                "Перевод отклонён" if rejected else "Перевод уже проведён"
            )
            await cq.answer("Отклонено" if rejected else "Уже проведено")
            return
        await self._execute_client_transfer(
            cq.message,
            source_message_id=parsed_source_message_id,
            recipient_id=parsed_recipient_id,
            amount=amount,
            currency=currency,
            allow_negative=phase == "confirm",
            actor_tg_user_id=cq.from_user.id,
        )
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.answer()

    @manager_or_admin_callback_required
    async def _cb_transfer_adjustment(self, cq: CallbackQuery) -> None:
        if not isinstance(cq.message, Message) or self.client_transfer_service is None:
            await cq.answer("Сообщение недоступно", show_alert=True)
            return
        if cq.data == "cta:reject":
            await cq.message.edit_reply_markup(reply_markup=None)
            await cq.answer("Отклонено")
            return
        try:
            _, raw_deal_id, raw_source_id, raw_amount = (cq.data or "").split(":", 3)
            deal_id = int(raw_deal_id)
            source_id = int(raw_source_id)
            new_amount = None if raw_amount == "cancel" else Decimal(raw_amount)
        except (ValueError, InvalidOperation):
            await cq.answer("Некорректное подтверждение", show_alert=True)
            return
        await self._execute_transfer_adjustment(
            cq.message, deal_id, new_amount,
            source_message_id=source_id, allow_negative=True,
            actor_tg_user_id=cq.from_user.id,
        )
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.answer()

    async def _on_currency_change(self, message: Message) -> None:
        if message.from_user and message.bot and message.from_user.id == message.bot.id:
            return

        if message.chat and message.chat.id in self.ignore_chat_ids:
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

        if (
            message.chat.id not in self.silent_chat_ids
            and not await require_manager_or_admin_message(
                self.repo,
                message,
                admin_chat_ids=self.admin_chat_ids,
                admin_user_ids=self.admin_user_ids,
            )
        ):
            return

        async with self.chat_locks.for_chat(message.chat.id):
            await self._execute_currency_change(message)

    async def _execute_currency_change(self, message: Message) -> None:
        raw_text = message.text or message.caption or ""
        if message.chat.id in self.silent_chat_ids:
            try:
                partner_send = (
                    self.interaction_service.wallet_service.parser.parse_partner_usdt_send(
                        raw_text
                    )
                )
            except ValueError:
                return
            if partner_send is not None:
                wallet_service = self.interaction_service.wallet_service
                common = {
                    "chat_id": message.chat.id,
                    "chat_name": get_chat_name(message),
                    "code": "USDT",
                    "source": "partner_chat_command",
                    "idempotency_key": f"{message.chat.id}:{message.message_id}",
                }
                if partner_send.amount is None:
                    await wallet_service.withdraw_all(
                        **common,
                        comment=partner_send.raw_text,
                    )
                else:
                    await wallet_service.apply_external_currency_change(
                        **common,
                        amount=-partner_send.amount,
                        expr=partner_send.raw_text,
                    )
                return
        try:
            parsed = self.interaction_service.wallet_service.parse_currency_change(
                raw_text,
                chat_id=message.chat.id,
            )
        except ValueError as exc:
            await self._answer(message, str(exc))
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
            await self._answer(
                message,
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
                await self._answer(message, str(exc))
                return
            suffix = " (повтор)" if settled.repeated else ""
            delivery_warning = ""
            client_chat_id = settled.client_chat_id
            if any(evidence_message.photo for evidence_message in evidence_messages):
                try:
                    client_chat_id = await send_cash_settlement_evidence_to_client(
                        repo=self.repo,
                        bot=message.bot,
                        evidence_messages=evidence_messages,
                        target_chat_id=settled.client_chat_id,
                        target_client_id=settled.client_id,
                    )
                except Exception:
                    log.exception(
                        "FAILED sending cash settlement evidence request_id=%s "
                        "client_id=%s chat_id=%s",
                        settled.request_id,
                        settled.client_id,
                        settled.client_chat_id,
                    )
                    delivery_warning = (
                        "\n⚠️ Не удалось отправить фото в чат клиента."
                    )
            signed_qty = (
                settled.actual_qty
                if settled.request_kind == "dep"
                else -settled.actual_qty
            )
            if (
                settled.client_balance is not None
                and settled.client_precision is not None
            ):
                try:
                    client_chat_id = await send_cash_settlement_balance_to_client(
                        repo=self.repo,
                        bot=message.bot,
                        target_chat_id=client_chat_id,
                        target_client_id=settled.client_id,
                        currency_code=settled.currency,
                        signed_amount=signed_qty,
                        balance=settled.client_balance,
                        precision=settled.client_precision,
                    )
                except Exception:
                    log.exception(
                        "FAILED sending cash settlement client balance "
                        "request_id=%s client_id=%s chat_id=%s",
                        settled.request_id,
                        settled.client_id,
                        client_chat_id,
                    )
                    delivery_warning += (
                        "\n⚠️ Не удалось отправить баланс в чат клиента."
                    )
            await self._answer(
                message,
                f"Заявка {settled.request_id} проведена{suffix}!\n"
                f"Клиент: {settled.client_name}{delivery_warning}",
            )
            await self._answer(
                message,
                "Запомнил. "
                f"{format_amount_core(signed_qty, settled.cash_precision)} "
                f"{settled.currency.lower()}\n"
                "Баланс: "
                f"{format_amount_core(settled.cash_balance, settled.cash_precision)} "
                f"{settled.currency.lower()}",
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
        await self._answer(message, text, reply_markup=result.reply_markup)

    async def _answer(self, message: Message, text: str, **kwargs: object) -> None:
        if message.chat.id in self.silent_chat_ids:
            return
        await message.answer(text, **kwargs)

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
        self.router.message.register(self._cmd_client_transfer, Command("перевод"))
        self.router.message.register(self._cmd_cancel_transfer, Command("отмена"))

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
        self.router.callback_query.register(self._cb_client_transfer, F.data.startswith("ct:"))
        self.router.callback_query.register(self._cb_transfer_adjustment, F.data.startswith("cta:"))
