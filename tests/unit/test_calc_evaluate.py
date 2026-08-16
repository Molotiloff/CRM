"""Expression calculator — выражения сумм («можно формулой», workflow.md 3.2).

Особая семантика процентов:
- в `+`/`-` процент берётся от левого операнда: 100+10% = 110;
- в `*`/`/` процент — просто доля: 200*50% = 100;
- скобочное выражение с %: (2+3)*100-50% = 500 - 250 = 250.
"""
from __future__ import annotations

from decimal import Decimal, getcontext

import pytest

from services.expression_calculator import CalcError, evaluate


class TestEvaluate:
    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("2+2", Decimal("4")),
            ("10-3*2", Decimal("4")),
            ("(10-3)*2", Decimal("14")),
            ("-5+8", Decimal("3")),
            ("1,5+1.5", Decimal("3.0")),          # запятая как десятичный разделитель
            ("100+10%", Decimal("110")),
            ("100-10%", Decimal("90")),
            ("200*50%", Decimal("100")),
            ("200/50%", Decimal("400")),
            ("(2+3)*100-50%", Decimal("250")),
            ("100+(5+5)%", Decimal("110")),        # суффиксный % после скобок
        ],
    )
    def test_valid_expressions(self, expr: str, expected: Decimal) -> None:
        assert evaluate(expr) == expected

    @pytest.mark.parametrize(
        "expr",
        ["", "   ", "abc", "2+*3", "(2+3", "10/0", "2;3", "2**3"],
    )
    def test_invalid_expressions_raise(self, expr: str) -> None:
        with pytest.raises(CalcError):
            evaluate(expr)

    def test_lone_percent_is_fraction(self) -> None:
        assert evaluate("50%") == Decimal("0.5")

    def test_evaluate_uses_local_decimal_precision(self) -> None:
        context = getcontext()
        original_precision = context.prec
        try:
            context.prec = 7
            result = evaluate("1/3")

            assert result == Decimal("0.3333333333333333333333333333")
            assert context.prec == 7
        finally:
            context.prec = original_precision
