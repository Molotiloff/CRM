from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .accounting import firm_position_currency
from .errors import DomainValidationError
from .value_objects import CurrencyCode


class AccountingAccount(StrEnum):
    CLIENT = "client"
    CITY_CASH = "city_cash"
    FIRM_POSITION = "firm_position"


@dataclass(frozen=True, slots=True)
class AccountingLeg:
    account: AccountingAccount
    currency: CurrencyCode
    amount: Decimal


class SettlementReviewStatus(StrEnum):
    MATCHED = "matched"
    NEEDS_REVIEW = "needs_review"
    RESOLVED = "resolved"


class SettlementResolution(StrEnum):
    ACCEPT_ACTUAL = "accept_actual"
    AMEND_UNPOSTED = "amend_unposted"
    CANCEL_AND_RECREATE = "cancel_and_recreate"


@dataclass(frozen=True, slots=True)
class SettlementComparison:
    expected: Decimal
    actual: Decimal
    delta: Decimal
    status: SettlementReviewStatus


def balance_sale_legs(
    *,
    rub_amount: Decimal,
    currency: CurrencyCode | str,
    quantity: Decimal,
) -> tuple[AccountingLeg, AccountingLeg]:
    fx = firm_position_currency(currency)
    _require_positive(rub_amount, "RUB amount")
    _require_positive(quantity, "currency quantity")
    return (
        AccountingLeg(AccountingAccount.CLIENT, CurrencyCode("RUB"), -rub_amount),
        AccountingLeg(AccountingAccount.CLIENT, fx, quantity),
    )


def office_deposit_legs(
    *, currency: CurrencyCode | str, quantity: Decimal
) -> tuple[AccountingLeg, AccountingLeg]:
    fx = firm_position_currency(currency)
    _require_positive(quantity, "deposit quantity")
    return (
        AccountingLeg(AccountingAccount.CITY_CASH, fx, quantity),
        AccountingLeg(AccountingAccount.CLIENT, fx, quantity),
    )


def office_withdrawal_legs(
    *, currency: CurrencyCode | str, quantity: Decimal
) -> tuple[AccountingLeg, AccountingLeg, AccountingLeg]:
    fx = firm_position_currency(currency)
    _require_positive(quantity, "withdrawal quantity")
    return (
        AccountingLeg(AccountingAccount.CITY_CASH, fx, -quantity),
        AccountingLeg(AccountingAccount.CLIENT, fx, -quantity),
        AccountingLeg(AccountingAccount.FIRM_POSITION, fx, -quantity),
    )


def compare_settlement(*, expected: Decimal, actual: Decimal) -> SettlementComparison:
    _require_positive(expected, "expected settlement quantity")
    _require_positive(actual, "actual settlement quantity")
    delta = actual - expected
    return SettlementComparison(
        expected=expected,
        actual=actual,
        delta=delta,
        status=(
            SettlementReviewStatus.MATCHED if delta == 0 else SettlementReviewStatus.NEEDS_REVIEW
        ),
    )


def validate_partner_allocations(
    *, purchase_quantity: Decimal, allocations: tuple[Decimal, ...]
) -> Decimal:
    _require_positive(purchase_quantity, "partner purchase quantity")
    for allocation in allocations:
        _require_positive(allocation, "partner allocation quantity")
    allocated = sum(allocations, Decimal(0))
    if allocated > purchase_quantity:
        raise DomainValidationError("Partner allocations exceed purchase quantity")
    return purchase_quantity - allocated


def capitalize_profit_components(
    *, free_quantity: Decimal, profit_quantity: Decimal
) -> tuple[Decimal, Decimal]:
    if profit_quantity < 0:
        raise DomainValidationError("Profit quantity must not be negative")
    return free_quantity + profit_quantity, Decimal(0)


def _require_positive(value: Decimal, field: str) -> None:
    if value <= 0:
        raise DomainValidationError(f"{field} must be greater than zero")
