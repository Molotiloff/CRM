from __future__ import annotations

import re
from collections.abc import Iterable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from services.client_balances import MINUS_CHARS, PLUS_CHARS, DailyBalancesReportService


class ClientsBalancesHandler:
    """
    /бк <ВАЛЮТА> <+|-> — клиенты с положительным/отрицательным балансом по валюте.
    /бк <ВАЛЮТА> — все клиенты с балансом по валюте, НО с фильтром |баланс| >= 1.
    /бк — все ненулевые балансы по всем валютам, сгруппировано по клиентам.
    """

    def __init__(
        self,
        report_service: DailyBalancesReportService,
        admin_chat_ids: Iterable[int] | None = None,
    ) -> None:
        self.report_service = report_service
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.router = Router()
        self._register()

    async def _cmd_balances(self, message: Message) -> None:
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Команда доступна только в админском чате.")
            return

        text = (message.text or "")

        m_with_sign = re.match(
            rf"(?iu)^/бк(?:@\w+)?\s+(\S+)\s+([{re.escape(MINUS_CHARS + PLUS_CHARS)}])\s*$",
            text,
        )
        m_only_code = re.match(
            r"(?iu)^/бк(?:@\w+)?\s+(\S+)\s*$",
            text,
        )

        if m_with_sign:
            code_filter = m_with_sign.group(1)
            sign_filter = m_with_sign.group(2)
            chunks = await self.report_service.build_report(
                code_filter=code_filter,
                sign_filter=sign_filter,
            )
            for chunk in chunks:
                await message.answer(chunk, parse_mode="HTML")
            return

        if m_only_code:
            code_filter = m_only_code.group(1)
            chunks = await self.report_service.build_report(code_filter=code_filter)
            for chunk in chunks:
                await message.answer(chunk, parse_mode="HTML")
            return

        chunks = await self.report_service.build_report()
        for chunk in chunks:
            await message.answer(chunk, parse_mode="HTML")

    def _register(self) -> None:
        self.router.message.register(self._cmd_balances, Command("бк"))
        self.router.message.register(
            self._cmd_balances,
            F.text.regexp(
                r"(?iu)^/бк(?:@\w+)?(?:\s+\S+(?:\s+[+\-−–—])?\s*)?$"
            ),
        )
