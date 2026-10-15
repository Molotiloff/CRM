from __future__ import annotations

import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from html import escape
from time import monotonic
from typing import cast

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from db_asyncpg.ports.administration import ManagerRepositoryPort
from domain.tron_wallet import is_probable_tron_wallet, normalize_wallet
from services.aml import AMLQueueFullError, AMLQueueService, AMLQueueTask
from services.aml.billing import AMLBillingPort
from services.aml.models import AMLCheckRequest, AMLNetwork

_RE_AML = re.compile(r"^/амл(?:@\w+)?(?:\s+(.+))?$", re.IGNORECASE)
_EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BTC_ADDRESS = re.compile(r"^(?:[13][1-9A-HJ-NP-Za-km-z]{25,34}|bc1[ac-hj-np-z02-9]{11,87})$", re.IGNORECASE)
_TX_HASH = re.compile(r"^[0-9a-fA-F]{64}$")
_USAGE = (
    "Формат: /амл <TRC20-адрес>, /амл erc20 (ерц) <адрес>, "
    "/амл bep20 (беп) <адрес>, /амл btc (бтс) <адрес> "
    "или /амл хэш <хэш TRC20-транзакции>"
)
_NETWORK_ALIASES: dict[str, AMLNetwork] = {
    "erc20": "erc20",
    "ерц": "erc20",
    "bep20": "bep20",
    "беп": "bep20",
    "btc": "btc",
    "бтс": "btc",
}


def parse_aml_request(text: str) -> AMLCheckRequest:
    match = _RE_AML.fullmatch(text.strip())
    args = match.group(1).split() if match and match.group(1) else []
    if len(args) == 1:
        wallet = normalize_wallet(args[0])
        if not is_probable_tron_wallet(wallet):
            raise ValueError("Похоже, это не TRON USDT-адрес. " + _USAGE)
        return AMLCheckRequest(value=wallet)
    if len(args) != 2:
        raise ValueError(_USAGE)

    kind, value = args[0].lower(), args[1].strip()
    network = _NETWORK_ALIASES.get(kind)
    if network in {"erc20", "bep20"}:
        if not _EVM_ADDRESS.fullmatch(value):
            raise ValueError(
                f"Нужен {network.upper()}-адрес формата 0x и 40 шестнадцатеричных символов."
            )
        return AMLCheckRequest(value=value, network=network)
    if network == "btc":
        if not _BTC_ADDRESS.fullmatch(value):
            raise ValueError("Похоже, это не Bitcoin-адрес.")
        return AMLCheckRequest(value=value, network="btc")
    if kind == "хэш":
        if not _TX_HASH.fullmatch(value):
            raise ValueError("Нужен хэш TRC20-транзакции: 64 шестнадцатеричных символа.")
        return AMLCheckRequest(value=value.lower(), kind="transaction")
    raise ValueError(_USAGE)


@dataclass(frozen=True, slots=True)
class _PendingCheck:
    request: AMLCheckRequest
    client_id: int
    chat_id: int
    user_id: int
    expires_at: float


class AMLHandler:
    def __init__(
        self,
        repo: ManagerRepositoryPort,
        *,
        aml_queue_service: AMLQueueService,
        billing: AMLBillingPort,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.aml_queue_service = aml_queue_service
        self.billing = billing
        self._pending: dict[str, _PendingCheck] = {}
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()
        self._register()

    async def _cmd_aml(self, message: Message) -> None:
        if message.chat.type not in {"group", "supergroup"}:
            await message.answer("⛔ AML-проверка доступна только в групповом чате, не в личных сообщениях с ботом.")
            return
        if message.from_user is None or message.from_user.is_bot or message.sender_chat is not None:
            await message.answer("⛔ Отправьте команду от своего имени.")
            return
        try:
            request = parse_aml_request(message.text or "")
        except ValueError as exc:
            await message.answer(str(exc))
            return

        user_id = message.from_user.id
        manager = user_id in self.admin_user_ids or await self.repo.is_manager(user_id)
        if not manager:
            client_id = await self.billing.wallet_client(message.chat.id)
            if client_id is None:
                await message.answer("⛔ AML-проверка доступна только в чате клиента с активным кошельком USDT.")
                return
            now = monotonic()
            self._pending = {key: value for key, value in self._pending.items() if value.expires_at > now}
            if len(self._pending) >= 1000:
                await message.answer("Слишком много ожидающих подтверждений. Попробуйте позже.")
                return
            token = secrets.token_hex(12)
            self._pending[token] = _PendingCheck(request, client_id, message.chat.id, user_id, now + 600)
            try:
                await message.answer(
                    "⚠️ AML-проверка стоит 1 USDT. Эта сумма будет списана с баланса вашего кошелька.\n"
                    "При недостатке средств баланс станет отрицательным или уменьшится ещё на 1 USDT.\n"
                    "Подтвердить проверку? (Подтверждение действует 10 минут.)",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="Подтвердить проверку — 1 USDT", callback_data=f"aml:yes:{token}"),
                        InlineKeyboardButton(text="Отмена", callback_data=f"aml:no:{token}"),
                    ]]),
                )
            except Exception:
                self._pending.pop(token, None)
                raise
            return
        await self._enqueue(message, request)

    async def _confirm(self, query: CallbackQuery) -> None:
        if not isinstance(query.message, Message):
            await query.answer("Сообщение недоступно", show_alert=True)
            return
        parts = (query.data or "").split(":")
        token = parts[-1]
        pending = self._pending.get(token)
        if pending is None or pending.expires_at <= monotonic():
            self._pending.pop(token, None)
            await query.answer("Подтверждение устарело или уже обработано. Отправьте /амл заново.", show_alert=True)
            return
        if query.from_user.id != pending.user_id or query.message.chat.id != pending.chat_id:
            await query.answer("Подтвердить или отменить проверку может только автор команды.", show_alert=True)
            return
        # Consume before the first await so concurrent clicks cannot enqueue twice.
        self._pending.pop(token)
        await query.answer()
        if parts[1] == "no":
            await query.message.edit_text("AML-проверка отменена. Средства не списаны.")
            return
        if await self.billing.wallet_client(pending.chat_id) != pending.client_id:
            await query.message.edit_text("⛔ Кошелёк недоступен. Средства не списаны.")
            return
        await query.message.edit_text("AML-проверка подтверждена. Стоимость: 1 USDT.")
        await self._enqueue(
            query.message, pending.request, client_id=pending.client_id,
            payment_key=f"aml:{pending.chat_id}:{query.message.message_id}",
        )

    async def _enqueue(
        self, message: Message, request: AMLCheckRequest, *,
        client_id: int | None = None, payment_key: str | None = None,
    ) -> None:
        wait_msg = await message.answer("⏳ AML-проверка добавлена в очередь...")
        bot = cast(Bot, message.bot)
        chat_id = message.chat.id
        wait_message_id = wait_msg.message_id

        async def on_success(result: dict) -> None:
            report = result["message_text"]
            if client_id is not None and payment_key is not None:
                balance = await self.billing.charge(
                    client_id=client_id, chat_id=chat_id, idempotency_key=payment_key,
                )
                report += f"\n\nСписано за AML-проверку: 1 USDT.\nБаланс: {balance:.2f} USDT"
            try:
                await bot.edit_message_text(
                    report,
                    chat_id=chat_id,
                    message_id=wait_message_id,
                )
            except TelegramAPIError:
                await bot.send_message(chat_id, report)

        async def on_error(exc: Exception) -> None:
            await bot.edit_message_text(
                f"❌ AML-проверка завершилась ошибкой:\n<code>{escape(str(exc))}</code>",
                chat_id=chat_id,
                message_id=wait_message_id,
                parse_mode="HTML",
            )

        try:
            position = await self.aml_queue_service.enqueue(
                AMLQueueTask(
                    request=request,
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
        self.router.callback_query.register(self._confirm, F.data.startswith("aml:yes:"))
        self.router.callback_query.register(self._confirm, F.data.startswith("aml:no:"))
