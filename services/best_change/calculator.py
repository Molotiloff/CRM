from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .models import (
    BestChangeAccount,
    BestChangeCalculation,
    BestChangeOperation,
    RecordBestChangeDeal,
)

_RUB_QUANTUM = Decimal("0.01")
_USDT_QUANTUM = Decimal("0.000001")
_PLATFORM_SHARE = Decimal("0.30")
_PROFIT_SHARE = Decimal("0.70")
_PARTNER_SHARE = Decimal("0.50")


class BestChangeCalculator:
    @staticmethod
    def calculate(command: RecordBestChangeDeal) -> BestChangeCalculation:
        unit_spread = (
            command.client_rate_rub - command.market_rate_rub
            if command.operation is BestChangeOperation.SALE
            else command.market_rate_rub - command.client_rate_rub
        )
        gross_spread = command.qty_usdt * unit_spread
        if gross_spread > 0:
            platform_fee_rub = gross_spread * _PLATFORM_SHARE
            platform_fee_usdt = _quantize(
                platform_fee_rub / command.client_rate_rub,
                _USDT_QUANTUM,
            )
            profit_pool = _quantize(gross_spread * _PROFIT_SHARE, _RUB_QUANTUM)
        else:
            platform_fee_rub = Decimal(0)
            platform_fee_usdt = Decimal(0).quantize(_USDT_QUANTUM)
            profit_pool = _quantize(gross_spread, _RUB_QUANTUM)
        partner_share = _quantize(profit_pool * _PARTNER_SHARE, _RUB_QUANTUM)
        skyex_profit = profit_pool - partner_share
        return BestChangeCalculation(
            operation=command.operation,
            city=command.city,
            qty_usdt=command.qty_usdt,
            market_rate_rub=command.market_rate_rub,
            client_rate_rub=command.client_rate_rub,
            unit_spread_rub=unit_spread,
            gross_spread_rub=gross_spread,
            platform_fee_rub_equivalent=platform_fee_rub,
            platform_fee_usdt=platform_fee_usdt,
            profit_pool_rub=profit_pool,
            partner_share_rub=partner_share,
            skyex_profit_rub=skyex_profit,
            profit_account=_profit_account(command.operation, command.city),
        )


def _profit_account(operation: BestChangeOperation, city: str) -> BestChangeAccount:
    return {
        (BestChangeOperation.PURCHASE, "тюм"): BestChangeAccount.PURCHASE_TYM,
        (BestChangeOperation.SALE, "тюм"): BestChangeAccount.SALE_TYM,
        (BestChangeOperation.PURCHASE, "члб"): BestChangeAccount.PURCHASE_CHLB,
        (BestChangeOperation.SALE, "члб"): BestChangeAccount.SALE_CHLB,
    }[operation, city]


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)
