from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from services.expression_calculator import CalcError, evaluate
from services.number_formatting import format_rate


@dataclass(slots=True, frozen=True)
class ExchangeCalculation:
    recv_code: str
    pay_code: str
    recv_amount_raw: Decimal
    pay_amount_raw: Decimal
    recv_amount: Decimal
    pay_amount: Decimal
    recv_precision: int
    pay_precision: int
    rate: Decimal
    rate_text: str


class ExchangeCalculator:
    _RUB_CODES = {"RUB", "РУБМСК", "РУБСПБ", "РУБПЕР", "РУБТЮМ"}

    @staticmethod
    def _find_account(accounts: Sequence[Mapping], code: str) -> Mapping | None:
        return next((row for row in accounts if str(row["currency_code"]).upper() == code), None)

    def calculate(
        self,
        *,
        recv_code: str,
        recv_amount_expr: str,
        pay_code: str,
        pay_amount_expr: str,
        accounts: Sequence[Mapping],
    ) -> ExchangeCalculation:
        recv_code = recv_code.strip().upper()
        pay_code = pay_code.strip().upper()

        try:
            recv_amount_raw = evaluate(recv_amount_expr)
            pay_amount_raw = evaluate(pay_amount_expr)
        except CalcError as e:
            raise ValueError(f"Ошибка в выражении: {e}") from e

        if recv_amount_raw <= 0 or pay_amount_raw <= 0:
            raise ValueError("Суммы должны быть > 0")

        acc_recv = self._find_account(accounts, recv_code)
        acc_pay = self._find_account(accounts, pay_code)
        if not acc_recv or not acc_pay:
            missing = recv_code if not acc_recv else pay_code
            raise ValueError(
                f"Счёт {missing} не найден. Добавьте валюту командой: /добавь {missing} [точность]"
            )

        recv_precision = int(acc_recv["precision"])
        pay_precision = int(acc_pay["precision"])

        q_recv = Decimal(10) ** -recv_precision
        q_pay = Decimal(10) ** -pay_precision
        recv_amount = recv_amount_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
        pay_amount = pay_amount_raw.quantize(q_pay, rounding=ROUND_HALF_UP)
        if recv_amount == 0 or pay_amount == 0:
            raise ValueError("Сумма слишком мала для точности выбранных валют.")

        try:
            if recv_code in self._RUB_CODES or pay_code in self._RUB_CODES:
                if recv_code in self._RUB_CODES:
                    rub_raw = recv_amount_raw
                    other_raw = pay_amount_raw
                else:
                    rub_raw = pay_amount_raw
                    other_raw = recv_amount_raw

                if other_raw == 0:
                    raise ValueError("Курс не определён (деление на ноль).")

                auto_rate = rub_raw / other_raw
            else:
                if recv_amount_raw == 0:
                    raise ValueError("Курс не определён (деление на ноль).")
                auto_rate = pay_amount_raw / recv_amount_raw

            if not auto_rate.is_finite() or auto_rate <= 0:
                raise ValueError("Курс невалидный.")

            rate = auto_rate.quantize(Decimal("1e-8"), rounding=ROUND_HALF_UP)
            rate_text = format_rate(rate)
        except ValueError:
            raise
        except (InvalidOperation, ZeroDivisionError) as e:
            raise ValueError("Ошибка расчёта курса.") from e

        return ExchangeCalculation(
            recv_code=recv_code,
            pay_code=pay_code,
            recv_amount_raw=recv_amount_raw,
            pay_amount_raw=pay_amount_raw,
            recv_amount=recv_amount,
            pay_amount=pay_amount,
            recv_precision=recv_precision,
            pay_precision=pay_precision,
            rate=rate,
            rate_text=rate_text,
        )
