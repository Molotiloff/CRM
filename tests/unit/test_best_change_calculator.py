from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from domain import DomainValidationError
from services.best_change import (
    BestChangeAccount,
    BestChangeCalculator,
    RecordBestChangeDeal,
)


def _command(**changes) -> RecordBestChangeDeal:
    values = {
        "operation": "sale",
        "city": "члб",
        "qty_usdt": Decimal("1000"),
        "market_rate_rub": Decimal("87"),
        "client_rate_rub": Decimal("88"),
        "actor_tg_user_id": 42,
        "chat_id": -100500,
        "message_id": 17,
        "deal_at": date(2026, 8, 31),
    }
    values.update(changes)
    return RecordBestChangeDeal(**values)


def test_sale_calculates_profit_and_usdt_commission() -> None:
    result = BestChangeCalculator.calculate(_command())

    assert result.gross_spread_rub == Decimal("1000")
    assert result.platform_fee_rub_equivalent == Decimal("300.00")
    assert result.platform_fee_usdt == Decimal("3.409091")
    assert result.profit_pool_rub == Decimal("700.00")
    assert result.partner_share_rub == Decimal("350.00")
    assert result.skyex_profit_rub == Decimal("350.00")
    assert result.profit_account is BestChangeAccount.SALE_CHLB


def test_purchase_uses_mirrored_spread_and_client_rate_for_commission() -> None:
    result = BestChangeCalculator.calculate(
        _command(
            operation="purchase",
            city="тюмень",
            market_rate_rub=Decimal("87"),
            client_rate_rub=Decimal("86"),
        )
    )

    assert result.gross_spread_rub == Decimal("1000")
    assert result.platform_fee_usdt == Decimal("3.488372")
    assert result.profit_account is BestChangeAccount.PURCHASE_TYM


def test_zero_spread_is_recordable_without_movements() -> None:
    result = BestChangeCalculator.calculate(
        _command(client_rate_rub=Decimal("87"))
    )

    assert result.gross_spread_rub == 0
    assert result.platform_fee_usdt == Decimal("0.000000")
    assert result.profit_pool_rub == Decimal("0.00")
    assert result.skyex_profit_rub == Decimal("0.00")


def test_loss_has_no_platform_fee_and_is_split_with_partner() -> None:
    result = BestChangeCalculator.calculate(
        _command(client_rate_rub=Decimal("86"))
    )

    assert result.is_loss is True
    assert result.gross_spread_rub == Decimal("-1000")
    assert result.platform_fee_usdt == Decimal("0.000000")
    assert result.profit_pool_rub == Decimal("-1000.00")
    assert result.partner_share_rub == Decimal("-500.00")
    assert result.skyex_profit_rub == Decimal("-500.00")


def test_rounding_is_applied_only_to_final_postings() -> None:
    result = BestChangeCalculator.calculate(
        _command(
            qty_usdt=Decimal("3"),
            market_rate_rub=Decimal("87.001"),
            client_rate_rub=Decimal("88.006"),
        )
    )

    assert result.unit_spread_rub == Decimal("1.005")
    assert result.gross_spread_rub == Decimal("3.015")
    assert result.profit_pool_rub == Decimal("2.11")
    assert result.partner_share_rub == Decimal("1.06")
    assert result.skyex_profit_rub == Decimal("1.05")
    assert result.platform_fee_usdt == Decimal("0.010278")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"city": "мск"}, "city"),
        ({"qty_usdt": Decimal(0)}, "quantity"),
        ({"market_rate_rub": Decimal("NaN")}, "market rate"),
        ({"actor_tg_user_id": 0}, "actor"),
    ],
)
def test_command_rejects_invalid_financial_inputs(changes, message: str) -> None:
    with pytest.raises(DomainValidationError, match=message):
        _command(**changes)
