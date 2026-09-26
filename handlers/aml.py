from __future__ import annotations

import re
from collections.abc import Iterable
from html import escape
from typing import cast

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.ports.administration import ManagerRepositoryPort
from domain.tron_wallet import is_probable_tron_wallet, normalize_wallet
from services.aml import AMLQueueFullError, AMLQueueService, AMLQueueTask
from services.aml.models import AMLCheckRequest, AMLNetwork
from telegram_adapters.auth import manager_or_admin_message_required

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
        try:
            request = parse_aml_request(message.text or "")
        except ValueError as exc:
            await message.answer(str(exc))
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
