from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from services.accounting.dashboard_comparison_service import (
    DashboardComparisonService,
    DifferenceKind,
)
from services.accounting.models import (
    MainDashboardCity,
    MainDashboardCurrency,
    MainDashboardOperations,
    MainDashboardPeriods,
    MainDashboardReconciliation,
    MainDashboardSnapshot,
)


def test_dashboard_comparison_applies_absolute_and_relative_tolerances() -> None:
    sheets = _snapshot(Decimal("100.00"))
    database = _snapshot(Decimal("100.009"))
    report = DashboardComparisonService(
        absolute_tolerance=Decimal("0.01"),
        relative_tolerance=Decimal("0.000001"),
    ).compare(sheets=sheets, database=database)

    assert report.status == "matched"
    assert report.mismatch_count == 0


def test_dashboard_comparison_classifies_sign_and_missing_mapping() -> None:
    sheets = _snapshot(Decimal("100"))
    database = replace(
        _snapshot(Decimal("-100")),
        currencies=(),
    )
    report = DashboardComparisonService().compare(sheets=sheets, database=database)

    kinds = {item.path: item.kind for item in report.differences}
    assert kinds["reconciliation.total_rub"] is DifferenceKind.SIGN
    assert kinds["currencies.USDT.physical_qty"] is DifferenceKind.MAPPING
    assert report.status == "mismatched"


def test_dashboard_comparison_includes_period_turnover() -> None:
    sheets = _snapshot(Decimal("100"))
    database = replace(
        sheets,
        periods=replace(sheets.periods, monthly_turnover=Decimal("10")),
    )

    report = DashboardComparisonService().compare(sheets=sheets, database=database)

    assert {item.path for item in report.differences} == {"periods.monthly_turnover"}


def test_dashboard_comparison_uses_observed_currency_fact_and_marks_manual_balances() -> None:
    sheets = replace(
        _snapshot(Decimal("100")),
        warnings=("Client balances and FX client amounts are manually maintained in Sheets",),
        reconciliation=replace(
            _snapshot(Decimal("100")).reconciliation,
            client_balances=Decimal("10"),
        ),
    )
    database_currency = replace(
        sheets.currencies[0],
        client_qty=Decimal("999"),
        fact_qty=Decimal("1009"),
        observed_qty=Decimal("15"),
    )
    database = replace(
        sheets,
        source="postgres",
        warnings=(),
        currencies=(database_currency,),
        reconciliation=replace(sheets.reconciliation, client_balances=Decimal("20")),
    )

    report = DashboardComparisonService().compare(sheets=sheets, database=database)

    differences = {item.path: item for item in report.differences}
    assert "currencies.USDT.physical_qty" not in differences
    assert differences["reconciliation.client_balances"].kind is DifferenceKind.STALE_EXTERNAL_DATA


def _snapshot(total_rub: Decimal) -> MainDashboardSnapshot:
    observed_at = datetime(2026, 8, 26, 12, tzinfo=UTC)
    return MainDashboardSnapshot(
        source="postgres",
        calculated_at=observed_at,
        data_as_of=observed_at,
        warnings=(),
        currencies=(
            MainDashboardCurrency(
                code="USDT",
                free_qty=Decimal("10"),
                internal_rate=Decimal("80"),
                rub_cost=Decimal("800"),
                client_qty=Decimal("5"),
                deal_profit_qty=Decimal(0),
                fact_qty=Decimal("15"),
                observed_qty=None,
                gap=None,
                observed_at=None,
            ),
        ),
        reconciliation=MainDashboardReconciliation(
            rub_cash=Decimal(0),
            rub_in_currency=Decimal("800"),
            total_rub=total_rub,
            client_balances=Decimal(0),
            skyex_balances=Decimal(0),
            total_balances=Decimal(0),
            fact_turnover=total_rub,
            accumulated_profit=Decimal(0),
            invested_capital=Decimal(0),
            turnover=Decimal(0),
            gap=total_rub,
            fact_rub=total_rub,
        ),
        periods=MainDashboardPeriods(
            daily_income=Decimal(0),
            daily_expense=Decimal(0),
            daily_profit=Decimal(0),
            daily_turnover=Decimal(0),
            monthly_turnover=Decimal(0),
            profitability=None,
        ),
        cities=(
            MainDashboardCity(
                city="екб",
                income=Decimal(0),
                expense=Decimal(0),
                profit=Decimal(0),
                active_requests=0,
            ),
        ),
        operations=MainDashboardOperations(
            active_requests=0,
            clients_with_balance=0,
            queued_usdt_qty=Decimal(0),
            queue_shortage_qty=Decimal(0),
            onchain_liquid_qty=None,
            profit_in_transit_qty=Decimal(0),
        ),
    )
