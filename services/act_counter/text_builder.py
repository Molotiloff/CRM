from __future__ import annotations

from decimal import Decimal

from services.number_formatting import format_amount_core


class ActCounterTextBuilder:
    def __init__(self, *, precision: int = 3) -> None:
        self.precision = precision

    def format_amount(self, amount: Decimal) -> str:
        return format_amount_core(amount, self.precision)

    def build_report_text(self, amount: Decimal) -> str:
        return f"USDT ACT: <code>{self.format_amount(amount)}</code>"

    def build_current_amount_text(self, amount: Decimal) -> str:
        return f"USDT ACT: <code>{self.format_amount(amount)}</code>"

    def build_reconcile_text(
        self,
        *,
        previous_amount: Decimal,
        current_amount: Decimal,
        delta: Decimal,
    ) -> str:
        lines = [
            f"ACT было: <code>{self.format_amount(previous_amount)}</code>",
            f"ACT стало: <code>{self.format_amount(current_amount)}</code>",
            f"Изменение: <code>{self.format_amount(delta)}</code>",
            "",
            f"Новая точка отсчёта сохранена: <code>{self.format_amount(current_amount)}</code>",
        ]

        return "\n".join(lines)
