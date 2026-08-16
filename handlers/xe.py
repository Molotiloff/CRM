from __future__ import annotations

import logging
from collections.abc import Iterable

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.ports.administration import ManagerRepositoryPort
from services.expression_calculator import expand_k_suffix
from services.xe_api import ConverterAPIError, ConverterAPIService
from services.xe_formatter import ResponseFormatter
from telegram_adapters.auth import manager_or_admin_message_required

log = logging.getLogger(__name__)


class XEHandler:
    def __init__(
        self,
        *,
        repo: ManagerRepositoryPort,
        converter_service: ConverterAPIService,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.converter_service = converter_service
        self.formatter = ResponseFormatter()
        self.router = Router()
        self._register()

    @manager_or_admin_message_required
    async def _cmd_xe(self, message: Message) -> None:
        raw_text = (message.text or "").strip()
        parts = raw_text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer(
                "Использование: /xe <FROM> <TO> <AMOUNT[%]>\n"
                "Например: /xe EUR USD 1000-0.3%"
            )
            return

        # «30k» → «30000» локально, чтобы не зависеть от внешнего Converter API.
        # Коды валют (буквы) под шаблон не попадают.
        query = expand_k_suffix(parts[1].strip())

        try:
            result = await self.converter_service.convert_text(
                text=query,
                include_image=True,
            )
        except ConverterAPIError as exc:
            await message.answer(f"❌ {exc}")
            return

        text = self.formatter.build_message_text(result)

        if result.image_url:
            try:
                await message.reply_photo(
                    photo=result.image_url,
                    caption=text,
                    parse_mode="HTML",
                )
                return
            except TelegramAPIError as exc:
                log.debug("reply_photo failed, falling back to text: %s", exc)

        await message.reply(text, parse_mode="HTML")

    def _register(self) -> None:
        self.router.message.register(self._cmd_xe, Command("xe"))
