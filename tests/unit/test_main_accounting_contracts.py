from __future__ import annotations

from decimal import Decimal

import pytest

from domain import (
    QUANTITY_QUANTUM,
    RATE_QUANTUM,
    RUB_QUANTUM,
    AccountingAccount,
    ClientRubValuationPolicy,
    DealStatus,
    DomainValidationError,
    SettlementReviewStatus,
    balance_sale_legs,
    calculate_reconciliation,
    capitalize_profit_components,
    compare_settlement,
    is_financially_posted,
    office_deposit_legs,
    office_withdrawal_legs,
    quantize_quantity,
    quantize_rate,
    quantize_rub,
    validate_partner_allocations,
    value_client_balance_in_rub,
)


def test_decimal_policy_rounds_only_at_explicit_boundary() -> None:
    raw = Decimal("10") / Decimal("3")

    assert Decimal("0.00000001") == QUANTITY_QUANTUM
    assert Decimal("0.000001") == RATE_QUANTUM
    assert Decimal("0.01") == RUB_QUANTUM
    assert raw != quantize_rate(raw)
    assert quantize_quantity(Decimal("1.000000005")) == Decimal("1.00000001")
    assert quantize_rate(raw) == Decimal("3.333333")
    assert quantize_rub(Decimal("10.005")) == Decimal("10.01")


@pytest.mark.parametrize("status", list(DealStatus))
def test_only_done_deal_is_financially_posted(status: DealStatus) -> None:
    assert is_financially_posted(status) is (status is DealStatus.DONE)


def test_balance_sale_changes_only_client_rub_and_fx() -> None:
    legs = balance_sale_legs(
        rub_amount=Decimal("11250"),
        currency="USD_BL",
        quantity=Decimal("125"),
    )

    assert [(leg.account, str(leg.currency), leg.amount) for leg in legs] == [
        (AccountingAccount.CLIENT, "RUB", Decimal("-11250")),
        (AccountingAccount.CLIENT, "USD_BL", Decimal("125")),
    ]


def test_office_deposit_and_withdrawal_keep_three_ledgers_distinct() -> None:
    deposit = office_deposit_legs(currency="USD_BL", quantity=Decimal("125"))
    withdrawal = office_withdrawal_legs(currency="USD_BL", quantity=Decimal("125"))

    assert [(leg.account, leg.amount) for leg in deposit] == [
        (AccountingAccount.CITY_CASH, Decimal("125")),
        (AccountingAccount.CLIENT, Decimal("125")),
    ]
    assert [(leg.account, leg.amount) for leg in withdrawal] == [
        (AccountingAccount.CITY_CASH, Decimal("-125")),
        (AccountingAccount.CLIENT, Decimal("-125")),
        (AccountingAccount.FIRM_POSITION, Decimal("-125")),
    ]


@pytest.mark.parametrize(
    ("actual", "delta", "status"),
    [
        (Decimal("500"), Decimal("0"), SettlementReviewStatus.MATCHED),
        (Decimal("490"), Decimal("-10"), SettlementReviewStatus.NEEDS_REVIEW),
        (Decimal("510"), Decimal("10"), SettlementReviewStatus.NEEDS_REVIEW),
    ],
)
def test_settlement_contract_preserves_actual_delta_and_review_status(
    actual: Decimal,
    delta: Decimal,
    status: SettlementReviewStatus,
) -> None:
    result = compare_settlement(expected=Decimal("500"), actual=actual)

    assert result.actual == actual
    assert result.delta == delta
    assert result.status is status


def test_partner_allocation_returns_only_firm_wallet_remainder() -> None:
    remainder = validate_partner_allocations(
        purchase_quantity=Decimal("10000"),
        allocations=(
            Decimal("1000"),
            Decimal("700"),
            Decimal("1300"),
            Decimal("800"),
            Decimal("1200"),
        ),
    )

    assert remainder == Decimal("5000")
    with pytest.raises(DomainValidationError, match="exceed"):
        validate_partner_allocations(
            purchase_quantity=Decimal("100"),
            allocations=(Decimal("60"), Decimal("50")),
        )


def test_midnight_capitalization_keeps_usdt_fact_unchanged() -> None:
    free_before = Decimal("1000")
    clients = Decimal("250")
    profit_before = Decimal("30")
    fact_before = free_before + clients + profit_before

    free_after, profit_after = capitalize_profit_components(
        free_quantity=free_before,
        profit_quantity=profit_before,
    )

    assert profit_after == 0
    assert free_after + clients + profit_after == fact_before


def test_unknown_client_fx_rub_value_is_null_with_warning_not_zero() -> None:
    rub = value_client_balance_in_rub(
        amount=Decimal("-500"),
        currency="RUB",
        policy=ClientRubValuationPolicy.STORED_RUB_ONLY,
    )
    fx = value_client_balance_in_rub(
        amount=Decimal("100"),
        currency="USDT",
        policy=ClientRubValuationPolicy.STORED_RUB_ONLY,
    )

    assert rub.amount == Decimal("-500")
    assert rub.warning is None
    assert fx.amount is None
    assert fx.warning == "RUB valuation is not configured for USDT"


def test_reconciliation_formula_contract_preserves_agreed_signs() -> None:
    result = calculate_reconciliation(
        rub_cash=Decimal("1000"),
        rub_in_currency=Decimal("250"),
        client_balances=Decimal("-100"),
        skyex_balances=Decimal("50"),
        invested_capital=Decimal("900"),
        income=Decimal("300"),
        expenses=Decimal("75"),
    )

    assert result.total_rub == Decimal("1250")
    assert result.total_balances == Decimal("-50")
    assert result.fact_turnover == Decimal("1300")
    assert result.accumulated_profit == Decimal("225")
    assert result.turnover == Decimal("1125")
    assert result.gap == Decimal("175")
    assert result.fact_rub == Decimal("1200")
