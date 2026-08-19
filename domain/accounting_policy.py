from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from .errors import DomainValidationError
from .value_objects import CurrencyCode, DealStatus

QUANTITY_QUANTUM = Decimal("0.00000001")
RATE_QUANTUM = Decimal("0.000001")
RUB_QUANTUM = Decimal("0.01")


def is_financially_posted(status: DealStatus | str) -> bool:
    normalized = status if isinstance(status, DealStatus) else DealStatus(status)
    return normalized is DealStatus.DONE


def quantize_quantity(value: Decimal) -> Decimal:
    return value.quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)


def quantize_rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def quantize_rub(value: Decimal) -> Decimal:
    return value.quantize(RUB_QUANTUM, rounding=ROUND_HALF_UP)


class ClientRubValuationPolicy(StrEnum):
    STORED_RUB_ONLY = "stored_rub_only"


@dataclass(frozen=True, slots=True)
class RubValuation:
    amount: Decimal | None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationAmounts:
    total_rub: Decimal
    total_balances: Decimal
    fact_turnover: Decimal
    accumulated_profit: Decimal
    turnover: Decimal
    gap: Decimal
    fact_rub: Decimal


def calculate_reconciliation(
    *,
    rub_cash: Decimal,
    rub_in_currency: Decimal,
    client_balances: Decimal,
    skyex_balances: Decimal,
    invested_capital: Decimal,
    income: Decimal,
    expenses: Decimal,
) -> ReconciliationAmounts:
    total_rub = rub_cash + rub_in_currency
    total_balances = client_balances + skyex_balances
    fact_turnover = total_rub - total_balances
    accumulated_profit = income - expenses
    turnover = invested_capital + accumulated_profit
    return ReconciliationAmounts(
        total_rub=total_rub,
        total_balances=total_balances,
        fact_turnover=fact_turnover,
        accumulated_profit=accumulated_profit,
        turnover=turnover,
        gap=fact_turnover - turnover,
        fact_rub=total_rub + total_balances,
    )


def value_client_balance_in_rub(
    *,
    amount: Decimal,
    currency: CurrencyCode | str,
    policy: ClientRubValuationPolicy = ClientRubValuationPolicy.STORED_RUB_ONLY,
) -> RubValuation:
    normalized = currency if isinstance(currency, CurrencyCode) else CurrencyCode(currency)
    if policy is not ClientRubValuationPolicy.STORED_RUB_ONLY:
        raise DomainValidationError(f"Unsupported client RUB valuation policy: {policy}")
    if normalized == CurrencyCode("RUB"):
        return RubValuation(amount=amount)
    return RubValuation(
        amount=None,
        warning=f"RUB valuation is not configured for {normalized}",
    )
