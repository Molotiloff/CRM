"""quantize_amount — округление денежных сумм (workflow.md 3.2)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from db_asyncpg.utils import quantize_amount


class TestQuantizeAmount:
    @pytest.mark.parametrize(
        ("value", "precision", "expected"),
        [
            ("1.005", 2, Decimal("1.01")),   # ROUND_HALF_UP, а не банковское
            ("1.004", 2, Decimal("1.00")),
            ("-1.005", 2, Decimal("-1.01")),  # HALF_UP симметричен от нуля
            ("2.5", 0, Decimal("3")),
            ("-2.5", 0, Decimal("-3")),
            ("123.456789", 8, Decimal("123.45678900")),
            ("0.123456785", 8, Decimal("0.12345679")),
        ],
    )
    def test_rounding_half_up(self, value: str, precision: int, expected: Decimal) -> None:
        assert quantize_amount(value, precision) == expected

    def test_accepts_decimal_str_int_float(self) -> None:
        assert quantize_amount(Decimal("10.1"), 2) == Decimal("10.10")
        assert quantize_amount("10.1", 2) == Decimal("10.10")
        assert quantize_amount(10, 2) == Decimal("10.00")
        # float идёт через str() — без двоичного мусора
        assert quantize_amount(10.1, 2) == Decimal("10.10")

    def test_result_exponent_matches_precision(self) -> None:
        assert quantize_amount("5", 2).as_tuple().exponent == -2
        assert quantize_amount("5.123", 0).as_tuple().exponent == 0
