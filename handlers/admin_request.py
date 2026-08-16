# handlers/admin_request.py
from __future__ import annotations

import html
import logging
import random
import re
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import asyncpg
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.ports.workflows import ClientWalletRepositoryPort
from services.expression_calculator import CalcError, evaluate
from services.messaging import MessengerError
from services.number_formatting import format_amount_core, format_rate
from telegram_adapters import AiogramMessenger

log = logging.getLogger(__name__)


class AdminRequestHandler:
    """
    /заявка <от_кого> <пд|пе|пт|пр> <recv_expr> <од|ое|от|ор> <pay_expr> [комментарий]

    ❗ Этот хэндлер НИЧЕГО не меняет в балансе.
    Он формирует карточку заявки и отправляет её:
      • в заявочный чат (request_chat_id)
      • и полную копию — в админский чат (текущий чат)
    """

    RECV_MAP = {"пд": "USD", "пе": "EUR", "пт": "USDT", "пр": "RUB"}
    PAY_MAP = {"од": "USD", "ое": "EUR", "от": "USDT", "ор": "RUB"}

    _RX = re.compile(
        r"""(?ixu)
        ^/заявка(?:@\w+)?\s+
        (?P<who>.+?)\s+
        /?(?P<recv_key>пд|пе|пт|пр)\s+
        (?P<recv_expr>.+?)\s+
        /?(?P<pay_key>од|ое|от|ор)\s+
        (?P<pay_expr>\S+)
        (?:\s+(?P<comment>.+))?
        \s*$
        """
    )

    def __init__(
        self,
        repo: ClientWalletRepositoryPort,
        *,
        admin_chat_id: int,
        request_chat_id: int,
        admin_user_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.admin_chat_id = int(admin_chat_id)
        self.request_chat_id = int(request_chat_id)
        self.admin_user_ids = {int(x) for x in (admin_user_ids or [])}
        self.router = Router()
        self._register()

    def _register(self) -> None:
        self.router.message.register(self._cmd_admin_request, Command("заявка"))
        self.router.message.register(
            self._cmd_admin_request, F.text.regexp(r"(?iu)^/заявка(?:@\w+)?\b")
        )

    async def _cmd_admin_request(self, message: Message) -> None:
        # Только в админском чате
        if message.chat.id != self.admin_chat_id:
            await message.answer("Команда доступна только в админском чате.")
            return

        raw = message.text or ""
        m = self._RX.match(raw)
        if not m:
            await message.answer(
                "Формат:\n"
                "  /заявка <от_кого> <пд|пе|пт|пр> <сумма/expr> <од|ое|от|ор> <сумма/expr> [комментарий]\n"
                "Например:\n"
                "  /заявка Клиент_Иванов пр 100000 от 100000/65 Приоритет\n"
                "  /заявка Петров /пт (700+300) /од 1000 срочно"
            )
            return

        who = (m.group("who") or "").strip()
        recv_key = m.group("recv_key").lower()
        recv_expr = (m.group("recv_expr") or "").strip()
        pay_key = m.group("pay_key").lower()
        pay_expr = (m.group("pay_expr") or "").strip()
        user_comment = (m.group("comment") or "").strip()

        recv_code = self.RECV_MAP.get(recv_key)
        pay_code = self.PAY_MAP.get(pay_key)
        if not recv_code or not pay_code:
            await message.answer("Не распознал валюты. Используйте пд/пе/пт/пр и од/ое/от/ор.")
            return

        # Проверка выражений
        try:
            recv_raw = evaluate(recv_expr)
            pay_raw = evaluate(pay_expr)
            if recv_raw <= 0 or pay_raw <= 0:
                await message.answer("Суммы должны быть > 0")
                return
        except (CalcError, InvalidOperation) as e:
            await message.answer(f"Ошибка в выражениях: {e}")
            return

        # Точности для красивого форматирования (пытаемся взять из кошелька админ-чата; если нет — дефолт)
        def default_precision(code: str) -> int:
            code = code.upper()
            if code in ("USDT",):
                return 2
            return 2

        recv_prec: int | None = None
        pay_prec: int | None = None
        try:
            client_id = await self.repo.ensure_client(chat_id=self.admin_chat_id, name="admin")
            accs = await self.repo.snapshot_wallet(client_id)
            for r in accs:
                c = str(r["currency_code"]).upper()
                if c == recv_code.upper():
                    recv_prec = int(r["precision"])
                if c == pay_code.upper():
                    pay_prec = int(r["precision"])
        except (asyncpg.PostgresError, KeyError, ValueError):
            # Best-effort: card is display-only (no balance change), fall back to defaults.
            log.warning(
                "Failed to read account precision for request card; using defaults", exc_info=True
            )

        recv_prec = recv_prec if recv_prec is not None else default_precision(recv_code)
        pay_prec = pay_prec if pay_prec is not None else default_precision(pay_code)

        # Квантуем для вывода
        q_recv = Decimal(10) ** -recv_prec
        q_pay = Decimal(10) ** -pay_prec
        recv_q = recv_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
        pay_q = pay_raw.quantize(q_pay, rounding=ROUND_HALF_UP)

        # Курс «как людям удобно»
        try:
            if recv_code.upper() == "RUB" or pay_code.upper() == "RUB":
                rub_raw = recv_raw if recv_code.upper() == "RUB" else pay_raw
                other_raw = pay_raw if recv_code.upper() == "RUB" else recv_raw
                rate = rub_raw / other_raw
            else:
                rate = pay_raw / recv_raw
            if not rate.is_finite() or rate <= 0:
                await message.answer("Курс невалидный.")
                return
            rate_str = format_rate(rate.quantize(Decimal("1e-8")))
        except (InvalidOperation, ZeroDivisionError):
            await message.answer("Ошибка расчёта курса.")
            return

        # Карточка заявки
        req_id = random.randint(10_000_000, 99_999_999)
        pretty_recv = format_amount_core(recv_q, recv_prec)
        pretty_pay = format_amount_core(pay_q, pay_prec)

        lines = [
            f"Заявка: <code>{req_id}</code>",
            f"Клиент: <b>{html.escape(who)}</b>",
            "-----",
            f"Получаем: <code>{pretty_recv} {recv_code.lower()}</code>",
            f"Курс: <code>{rate_str}</code>",
            f"Отдаём: <code>{pretty_pay} {pay_code.lower()}</code>",
            "----",
            f"Формула: <code>{html.escape(pay_expr)}</code>",
        ]
        if user_comment:
            lines.append(f"----\nКомментарий: <code>{html.escape(user_comment)}</code>")

        request_text = "\n".join(lines)

        # 1) В заявочный чат
        try:
            await AiogramMessenger(message.bot).send(
                chat_id=self.request_chat_id,
                text=request_text,
                reply_markup=None,
            )
        except MessengerError as e:
            log.exception("Failed to post request card to request chat %s", self.request_chat_id)
            await message.answer(f"Не удалось отправить заявку в заявочный чат: {e}")
            return

        # 2) Полная карточка — в админский чат (текущий)
        await message.answer(request_text, parse_mode="HTML")
