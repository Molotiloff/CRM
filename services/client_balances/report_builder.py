from __future__ import annotations

import html
from decimal import Decimal

from services.client_balances.filter_service import ClientBalanceGroup
from services.client_balances.query_service import ClientBalanceRow
from services.number_formatting import format_amount_core


class ClientBalancesReportBuilder:
    @staticmethod
    def _chunk(text: str, limit: int = 3500) -> list[str]:
        out, cur, total = [], [], 0
        for line in text.splitlines(True):
            if total + len(line) > limit and cur:
                out.append("".join(cur))
                cur, total = [], 0
            cur.append(line)
            total += len(line)
        if cur:
            out.append("".join(cur))
        return out

    @staticmethod
    def _balance_line(balance: Decimal, precision: int, code: str) -> str:
        pretty = html.escape(format_amount_core(balance, precision))
        return f"  {pretty} {html.escape(code.lower())}"

    def build_signed_report(
        self,
        *,
        code_filter: str,
        sign_filter: str,
        rows: list[ClientBalanceRow],
        min_negative_balance: Decimal | None = None,
        min_positive_balance: Decimal | None = None,
    ) -> list[str]:
        if not rows:
            if sign_filter == "-" and min_negative_balance is not None:
                return [
                    f"Нет клиентов по {html.escape(code_filter)} "
                    f"с балансом меньше {html.escape(str(min_negative_balance))}."
                ]
            if sign_filter == "+" and min_positive_balance is not None:
                return [
                    f"Нет клиентов по {html.escape(code_filter)} "
                    f"с балансом больше {html.escape(str(min_positive_balance))}."
                ]
            cmp_html = "&gt; 0" if sign_filter == "+" else "&lt; 0"
            return [f"Нет клиентов по {html.escape(code_filter)} ({cmp_html})."]

        if sign_filter == "-" and min_negative_balance is not None:
            header = (
                f"Клиенты по {html.escape(code_filter)} "
                f"(баланс меньше {html.escape(str(min_negative_balance))}):"
            )
        elif sign_filter == "+" and min_positive_balance is not None:
            header = (
                f"Клиенты по {html.escape(code_filter)} "
                f"(баланс больше {html.escape(str(min_positive_balance))}):"
            )
        else:
            cmp_html = "&gt; 0" if sign_filter == "+" else "&lt; 0"
            header = f"Клиенты по {html.escape(code_filter)} ({cmp_html}):"

        lines = [header, ""]
        for row in rows:
            lines.append(html.escape(row.client_name))
            lines.append(self._balance_line(row.balance, row.precision, code_filter))
            lines.append("—————————")
        return self._chunk("\n".join(lines))

    def build_code_report(
        self,
        *,
        code_filter: str,
        rows: list[ClientBalanceRow],
        near_zero_threshold: Decimal,
    ) -> list[str]:
        if not rows:
            return [
                f"Нет клиентов с балансом по {html.escape(code_filter)} "
                f"(|баланс| ≥ {near_zero_threshold})."
            ]

        lines = [
            f"Клиенты по {html.escape(code_filter)} (|баланс| ≥ {near_zero_threshold}):",
            "",
        ]
        for row in rows:
            lines.append(html.escape(row.client_name))
            lines.append(self._balance_line(row.balance, row.precision, code_filter))
            lines.append("—————————")
        return self._chunk("\n".join(lines))

    def build_full_report(self, groups: list[ClientBalanceGroup]) -> list[str]:
        if not groups:
            return ["У всех клиентов нулевые балансы."]

        lines = ["Все ненулевые балансы:", ""]
        for group in groups:
            lines.append(html.escape(group.name))
            for code, precision, balance in group.items:
                lines.append(self._balance_line(balance, precision, code))
            lines.append("—————————")
        return self._chunk("\n".join(lines))
