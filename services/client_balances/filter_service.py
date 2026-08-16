from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from services.client_balances.query_service import ClientBalanceRow

MINUS_CHARS = "-−–—"
PLUS_CHARS = "+＋"

ALIASES = {
    "RUB": "RUB",
    "РУБ": "RUB",
    "РУБЛЬ": "RUB",
    "РУБЛИ": "RUB",
    "РУБЛЕЙ": "RUB",
    "РУБ.": "RUB",
    "USD": "USD",
    "ДОЛ": "USD",
    "ДОЛЛ": "USD",
    "ДОЛЛАР": "USD",
    "ДОЛЛАРЫ": "USD",
    "USDT": "USDT",
    "ЮСДТ": "USDT",
    "EUR": "EUR",
    "ЕВРО": "EUR",
    "USDW": "USDW",
    "ДОЛБ": "USDW",
    "ДОЛЛБЕЛ": "USDW",
    "ДОЛБЕЛ": "USDW",
}

NEAR_ZERO_THRESHOLD = Decimal("1")


@dataclass(slots=True)
class ClientBalanceGroup:
    name: str = ""
    chat_id: int | None = None
    items: list[tuple[str, int, Decimal]] = field(default_factory=list)


class ClientBalancesFilterService:
    near_zero_threshold = NEAR_ZERO_THRESHOLD

    @staticmethod
    def normalize_code(code: str) -> str:
        code_up = (code or "").strip().upper()
        return ALIASES.get(code_up, code_up)

    @staticmethod
    def normalize_sign(ch: str) -> str:
        value = (ch or "").strip()
        if value in MINUS_CHARS:
            return "-"
        if value in PLUS_CHARS:
            return "+"
        return value

    def filter_by_code_and_sign(
        self,
        rows: Iterable[ClientBalanceRow],
        *,
        code_filter: str,
        sign_filter: str,
        min_negative_balance: Decimal | None = None,
        min_positive_balance: Decimal | None = None,
        excluded_client_group: str | None = None,
    ) -> tuple[str, str, list[ClientBalanceRow]]:
        normalized_code = self.normalize_code(code_filter)
        normalized_sign = self.normalize_sign(sign_filter)
        excluded_group_norm = (excluded_client_group or "").strip().casefold()

        filtered: list[ClientBalanceRow] = []
        for row in rows:
            if row.currency_code != normalized_code:
                continue
            if excluded_group_norm and row.client_group.casefold() == excluded_group_norm:
                continue

            balance = row.balance
            if normalized_sign == "+":
                if balance <= 0:
                    continue
                if min_positive_balance is not None and balance <= min_positive_balance:
                    continue
            elif normalized_sign == "-":
                if balance >= 0:
                    continue
                if min_negative_balance is not None and balance >= min_negative_balance:
                    continue

            filtered.append(row)

        if normalized_sign == "-":
            filtered.sort(key=lambda row: (row.balance, row.client_name.lower()))
        else:
            filtered.sort(key=lambda row: (-row.balance, row.client_name.lower()))

        return normalized_code, normalized_sign, filtered

    def filter_by_code(
        self,
        rows: Iterable[ClientBalanceRow],
        *,
        code_filter: str,
    ) -> tuple[str, list[ClientBalanceRow]]:
        normalized_code = self.normalize_code(code_filter)
        filtered = [
            row
            for row in rows
            if row.currency_code == normalized_code
            and row.balance.copy_abs() >= self.near_zero_threshold
        ]
        filtered.sort(key=lambda row: row.client_name.lower())
        return normalized_code, filtered

    def group_nonzero_by_client(
        self,
        rows: Iterable[ClientBalanceRow],
    ) -> list[ClientBalanceGroup]:
        by_client: dict[int, ClientBalanceGroup] = defaultdict(ClientBalanceGroup)
        for row in rows:
            if row.balance == Decimal("0"):
                continue

            group = by_client[row.client_id]
            group.name = row.client_name
            group.chat_id = row.chat_id
            group.items.append((self.normalize_code(row.currency_code), row.precision, row.balance))

        groups = list(by_client.values())
        groups.sort(key=lambda group: group.name.lower())
        for group in groups:
            group.items.sort()
        return groups
