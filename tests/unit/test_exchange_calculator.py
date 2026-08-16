"""ExchangeCalculator — расчёт обмена и курса (workflow.md 3.2)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from services.exchange.calculator import ExchangeCalculator


def _accounts(*rows: tuple[str, int]) -> list[dict]:
    return [{"currency_code": code, "precision": prec} for code, prec in rows]


ACCOUNTS = _accounts(("RUB", 2), ("USDT", 2), ("EUR", 2), ("BTC", 8))


class TestCalculate:
    def setup_method(self) -> None:
        self.calc = ExchangeCalculator()

    def test_rub_pair_rate_is_rub_per_unit(self) -> None:
        # Получаем USDT, отдаём RUB: курс = руб / валюта, независимо от стороны
        res = self.calc.calculate(
            recv_code="usdt", recv_amount_expr="100",
            pay_code="rub", pay_amount_expr="9000",
            accounts=ACCOUNTS,
        )
        assert res.recv_code == "USDT" and res.pay_code == "RUB"
        assert res.rate == Decimal("90.00000000")
        assert res.recv_amount == Decimal("100.00")
        assert res.pay_amount == Decimal("9000.00")

    def test_rub_on_recv_side_same_rate_semantics(self) -> None:
        res = self.calc.calculate(
            recv_code="rub", recv_amount_expr="9000",
            pay_code="usdt", pay_amount_expr="100",
            accounts=ACCOUNTS,
        )
        assert res.rate == Decimal("90.00000000")

    def test_regional_rub_codes_count_as_rub(self) -> None:
        accounts = _accounts(("РУБМСК", 2), ("USDT", 2))
        res = self.calc.calculate(
            recv_code="usdt", recv_amount_expr="10",
            pay_code="рубмск", pay_amount_expr="950",
            accounts=accounts,
        )
        assert res.rate == Decimal("95.00000000")

    def test_non_rub_pair_rate_is_pay_per_recv(self) -> None:
        res = self.calc.calculate(
            recv_code="eur", recv_amount_expr="100",
            pay_code="usdt", pay_amount_expr="108",
            accounts=ACCOUNTS,
        )
        assert res.rate == Decimal("1.08000000")

    def test_amount_expressions_are_evaluated(self) -> None:
        res = self.calc.calculate(
            recv_code="usdt", recv_amount_expr="50*2",
            pay_code="rub", pay_amount_expr="100*90",
            accounts=ACCOUNTS,
        )
        assert res.recv_amount == Decimal("100.00")
        assert res.pay_amount == Decimal("9000.00")

    def test_amounts_quantized_to_account_precision(self) -> None:
        res = self.calc.calculate(
            recv_code="btc", recv_amount_expr="0.123456789",  # BTC prec 8
            pay_code="rub", pay_amount_expr="1000.005",       # RUB prec 2
            accounts=ACCOUNTS,
        )
        assert res.recv_amount == Decimal("0.12345679")
        assert res.pay_amount == Decimal("1000.01")
        # курс считается от сырых сумм, не от квантованных
        assert res.recv_amount_raw == Decimal("0.123456789")

    @pytest.mark.parametrize(
        ("recv_expr", "pay_expr", "fragment"),
        [
            ("2+*3", "100", "Ошибка в выражении"),
            ("0", "100", "должны быть > 0"),
            ("-5", "100", "должны быть > 0"),
        ],
    )
    def test_bad_amounts(self, recv_expr: str, pay_expr: str, fragment: str) -> None:
        with pytest.raises(ValueError, match=fragment):
            self.calc.calculate(
                recv_code="usdt", recv_amount_expr=recv_expr,
                pay_code="rub", pay_amount_expr=pay_expr,
                accounts=ACCOUNTS,
            )

    def test_missing_account_names_missing_code(self) -> None:
        with pytest.raises(ValueError, match="Счёт XYZ не найден"):
            self.calc.calculate(
                recv_code="xyz", recv_amount_expr="1",
                pay_code="rub", pay_amount_expr="100",
                accounts=ACCOUNTS,
            )

    def test_amount_below_precision_rejected(self) -> None:
        # 0.001 при точности 2 квантуется в 0.00 → отказ
        with pytest.raises(ValueError, match="слишком мала"):
            self.calc.calculate(
                recv_code="usdt", recv_amount_expr="0.001",
                pay_code="rub", pay_amount_expr="100",
                accounts=ACCOUNTS,
            )
