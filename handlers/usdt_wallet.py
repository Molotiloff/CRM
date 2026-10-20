# handlers/usdt_wallet.py
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.admin_client import UsdtWalletService
from services.admin_client.usdt_wallet_service import WalletSlot


class UsdtWalletHandler:
    def __init__(self, service: UsdtWalletService, *, admin_chat_ids: set[int] | None = None) -> None:
        self.service = service
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.router = Router()
        self._register()

    # --- показать адрес ---
    async def _cmd_show(self, message: Message) -> None:
        # просмотр доступен всем
        await message.answer(await self.service.build_show_message(), parse_mode="HTML")

    async def _cmd_show2(self, message: Message) -> None:
        await message.answer(await self.service.build_show_message(wallet_slot=2), parse_mode="HTML")

    # --- задать адрес ---
    async def _cmd_set(self, message: Message) -> None:
        await self._set_wallet(message, wallet_slot=1)

    async def _cmd_set2(self, message: Message) -> None:
        await self._set_wallet(message, wallet_slot=2)

    async def _set_wallet(self, message: Message, *, wallet_slot: WalletSlot) -> None:
        # менять можно только из админского чата
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Эту команду можно использовать только в админском чате.")
            return

        await message.answer(await self.service.set_from_text(message.text or "", wallet_slot=wallet_slot))

    def _register(self) -> None:
        # показать: /кош и /usdt
        self.router.message.register(self._cmd_show, Command("кош"))
        self.router.message.register(self._cmd_show2, Command("кош2"))
        # задать: /setкош и /setwallet
        self.router.message.register(self._cmd_set, Command("setwallet"))
        self.router.message.register(self._cmd_set2, Command("setwallet2"))
