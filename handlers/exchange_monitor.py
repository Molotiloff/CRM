from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.ports.administration import ManagerRepositoryPort
from services.rate_order import OrderbookService
from telegram_adapters.auth import manager_or_admin_message_required


class ExchangeMonitorHandler:
    def __init__(
        self,
        repo: ManagerRepositoryPort,
        *,
        orderbook_service: OrderbookService,
        command_prefix: str,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
    ) -> None:
        self.command_prefix = command_prefix
        self.repo = repo
        self.orderbook_service = orderbook_service
        self.admin_chat_ids = {int(x) for x in (admin_chat_ids or [])}
        self.admin_user_ids = {int(x) for x in (admin_user_ids or [])}
        self.router = Router()
        self._register()

    def _is_admin_chat(self, chat_id: int) -> bool:
        return int(chat_id) in self.admin_chat_ids

    @manager_or_admin_message_required
    async def _cmd_asks(self, message: Message) -> None:
        text = self.orderbook_service.build_asks_depth_text(
            min_total_volume=Decimal("500000"),
        )
        await message.answer(text)

    @manager_or_admin_message_required
    async def _cmd_bids(self, message: Message) -> None:
        text = self.orderbook_service.build_bids_depth_text(
            min_total_volume=Decimal("500000"),
        )
        await message.answer(text)

    @manager_or_admin_message_required
    async def _cmd_first_bid(self, message: Message) -> None:
        text = self.orderbook_service.build_first_bid_text()
        await message.answer(text)

    @manager_or_admin_message_required
    async def _cmd_live(self, message: Message) -> None:
        if not self._is_admin_chat(message.chat.id):
            await message.answer(f"Команда /{self.command_prefix}ред доступна только в админском чате.")
            return

        text = self.orderbook_service.build_live_text(
            min_total_volume=Decimal("500000"),
        )
        sent = await message.answer(text)
        await self.orderbook_service.set_live_message(
            chat_id=sent.chat.id,
            message_id=sent.message_id,
        )

    def _register(self) -> None:
        self.router.message.register(self._cmd_asks, Command(self.command_prefix))
        self.router.message.register(self._cmd_bids, Command(f"{self.command_prefix}п"))
        self.router.message.register(self._cmd_first_bid, Command(f"{self.command_prefix}-"))
        self.router.message.register(self._cmd_live, Command(f"{self.command_prefix}ред"))
