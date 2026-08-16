from __future__ import annotations

import html
import re

from db_asyncpg.ports.workflows import ClientWalletTransactionRepositoryPort
from services.wallets.compact_formatter import format_wallet_compact

_DISPLAY_NAMES_RU = {
    "USD": "дол",
    "USDT": "юсдт",
    "EUR": "евро",
    "RUB": "руб",
    "USDW": "долб",
}

_CURRENCY_ALIASES = {
    "usd": "USD",
    "дол": "USD",
    "долл": "USD",
    "доллар": "USD",
    "доллары": "USD",
    "usdt": "USDT",
    "юсдт": "USDT",
    "eur": "EUR",
    "евро": "EUR",
    "rub": "RUB",
    "руб": "RUB",
    "рубль": "RUB",
    "рубли": "RUB",
    "рублей": "RUB",
    "руб.": "RUB",
    "usdw": "USDW",
    "долб": "USDW",
    "доллбел": "USDW",
    "долбел": "USDW",
}


class NonZeroWalletQueryService:
    def __init__(self, repo: ClientWalletTransactionRepositoryPort) -> None:
        self.repo = repo

    @staticmethod
    def _display_code_ru(code: str) -> str:
        return _DISPLAY_NAMES_RU.get(code.upper(), code).lower()

    @staticmethod
    def _normalize_code_alias(raw: str) -> str:
        key = (raw or "").strip().lower()
        return _CURRENCY_ALIASES.get(key, (raw or "").strip().upper())

    async def build_wallet_message(self, *, command_text: str, chat_id: int, chat_name: str) -> str:
        match = re.match(r"(?iu)^/дай(?:@\w+)?(?:\s+(\S+))?\s*$", (command_text or "").strip())
        arg_code = match.group(1) if match else None

        client_id = await self.repo.ensure_client(chat_id, chat_name)
        rows = await self.repo.snapshot_wallet(client_id)
        if not rows:
            return "Нет счетов. Добавьте валюту: /добавь USD 2"

        if arg_code:
            code = self._normalize_code_alias(arg_code)
            acc = next((row for row in rows if str(row["currency_code"]).upper() == code), None)
            if not acc:
                return f"Счёт {code} не найден. Добавьте валюту: /добавь {code} [точность]"

            single_row = [{
                "currency_code": self._display_code_ru(code),
                "balance": acc.get("balance"),
                "precision": int(acc.get("precision", 2)),
            }]
            compact_one = format_wallet_compact(single_row, only_nonzero=False)
            safe_title = html.escape(f"Средств у {chat_name}:")
            safe_rows = html.escape(compact_one)
            return f"<code>{safe_title}\n\n{safe_rows}</code>"

        renamed = []
        for row in rows:
            item = dict(row)
            item["currency_code"] = self._display_code_ru(str(row["currency_code"]))
            renamed.append(item)

        compact = format_wallet_compact(renamed, only_nonzero=True)
        if compact == "Пусто":
            return "Все счета нулевые. Посмотреть всё: /кошелек"

        safe_title = html.escape(f"Средств у {chat_name}:")
        safe_rows = html.escape(compact)
        return f"<code>{safe_title}\n\n{safe_rows}</code>"
