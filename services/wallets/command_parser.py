from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from services.expression_calculator import CalcError, evaluate
from services.wallets.models import ParsedCurrencyChange


class WalletCommandParser:
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
        "рубль.": "RUB",
        "usdw": "USDW",
        "долб": "USDW",
        "доллбел": "USDW",
        "долбел": "USDW",
        "eur500": "EUR500",
        "евро500": "EUR500",
    }

    def __init__(self, *, city_cash_chats: Mapping[str, int] | None = None) -> None:
        self.city_cash_chats = {
            str(city).strip().lower(): int(chat_id)
            for city, chat_id in (city_cash_chats or {}).items()
        }
        self._city_by_chat_id = {
            chat_id: city for city, chat_id in self.city_cash_chats.items()
        }

    @classmethod
    def normalize_code_alias(cls, raw_code: str) -> str:
        key = (raw_code or "").strip().lower()
        alias = cls._CURRENCY_ALIASES.get(key)
        if alias:
            return alias
        if key in ("руб", "рубль", "рубли", "рублей", "руб."):
            return "RUB"
        return (raw_code or "").strip().upper()

    @staticmethod
    def extract_expr_prefix(s: str) -> str:
        if not s:
            return ""
        first = s.strip().split(maxsplit=1)[0]
        return first.replace(",", ".")

    @staticmethod
    def split_city_transfer_tail(tail: str) -> tuple[str, str]:
        s = (tail or "").strip()
        if not s:
            return "", ""
        left, sep, right = s.partition("!")
        client_name = left.strip()
        comment = right.strip() if sep else ""
        return client_name, comment

    def parse_currency_change(
        self,
        raw_text: str,
        *,
        chat_id: int,
    ) -> ParsedCurrencyChange | None:
        text = raw_text.strip()
        if not text.startswith("/"):
            return None

        parts = text[1:].split(None, 1)
        if len(parts) < 2:
            return None

        raw_code = parts[0]
        code = self.normalize_code_alias(raw_code)

        expr_full = parts[1].strip()
        expr = self.extract_expr_prefix(expr_full)
        if not expr:
            raise ValueError("Сумма не указана. Пример: /USD 250")

        first_token = expr_full.strip().split(maxsplit=1)[0]
        tail = expr_full[len(first_token) :].strip()

        try:
            amount = evaluate(expr)
        except CalcError as e:
            raise ValueError(f"Ошибка в выражении суммы: {e}") from e

        if amount == 0:
            raise ValueError("Сумма должна быть ненулевой")

        cash_city = self._city_by_chat_id.get(chat_id)
        is_city_cash = cash_city is not None

        if is_city_cash:
            client_name_for_transfer, extra_comment = self.split_city_transfer_tail(tail)
        else:
            client_name_for_transfer, extra_comment = "", tail

        return ParsedCurrencyChange(
            code=code,
            expr=expr,
            amount=Decimal(amount),
            tail=tail,
            is_city_cash=is_city_cash,
            cash_city=cash_city,
            client_name_for_transfer=client_name_for_transfer,
            extra_comment=extra_comment,
        )
