from __future__ import annotations

import html
from decimal import Decimal

from services.number_formatting import format_amount_core, format_amount_with_sign
from services.wallets.compact_formatter import format_wallet_compact


class WalletTextBuilder:
    @staticmethod
    def wallet_text(*, chat_name: str, rows: list[dict]) -> str:
        safe_title = html.escape(f"Средств у {chat_name}:")
        safe_rows = html.escape(format_wallet_compact(rows, only_nonzero=False))
        return f"<code>{safe_title}\n\n{safe_rows}</code>"

    @staticmethod
    def remove_currency_confirmation(*, code: str, balance: Decimal, precision: int) -> str:
        pretty_bal = format_amount_core(balance, precision)
        warn = ""
        if balance != 0:
            warn = (
                f"\n⚠️ Внимание: баланс по {code} не нулевой ({pretty_bal} {code.lower()}). "
                f"Удаление допустимо — остаток будет потерян."
            )
        return f"Вы уверены, что хотите удалить валюту {code}?{warn}"

    @staticmethod
    def currency_change_success(
        *,
        code: str,
        delta: Decimal,
        precision: int,
        sign: str,
        balance: Decimal,
    ) -> str:
        pretty_amt = format_amount_with_sign(delta, precision, sign=sign)
        pretty_bal = format_amount_core(balance, precision)
        return f"Запомнил. {pretty_amt}\nБаланс: {pretty_bal} {code.lower()}"

    @staticmethod
    def undo_already_done_with_balance(*, code: str, balance: Decimal, precision: int) -> str:
        pretty_bal = format_amount_core(balance, precision)
        return f"Операция уже отменена\nБаланс: {pretty_bal} {code.lower()}"

    @staticmethod
    def undo_success(
        *,
        code: str,
        amount: Decimal,
        precision: int,
        applied_sign: str,
        balance: Decimal,
    ) -> str:
        pretty_delta = format_amount_with_sign(amount, precision, sign=applied_sign)
        pretty_bal = format_amount_core(balance, precision)
        return f"Запомнил. {pretty_delta}\nБаланс: {pretty_bal} {code.lower()}"
