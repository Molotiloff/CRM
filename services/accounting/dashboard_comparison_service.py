from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from .models import MainDashboardSnapshot


class DifferenceKind(StrEnum):
    MAPPING = "mapping"
    MISSING_HISTORY = "missing_history"
    TIMESTAMP_SKEW = "timestamp_skew"
    SIGN = "sign"
    ROUNDING = "rounding"
    STALE_EXTERNAL_DATA = "stale_external_data"
    ACCOUNTING_ERROR = "real_accounting_error"


@dataclass(frozen=True, slots=True, kw_only=True)
class DashboardDifference:
    path: str
    sheet_value: Decimal | None
    db_value: Decimal | None
    absolute_delta: Decimal | None
    relative_delta: Decimal | None
    kind: DifferenceKind


@dataclass(frozen=True, slots=True, kw_only=True)
class DashboardComparisonReport:
    status: str
    compared_fields: int
    differences: tuple[DashboardDifference, ...]
    absolute_tolerance: Decimal
    relative_tolerance: Decimal
    sheets_data_as_of: datetime
    db_data_as_of: datetime

    @property
    def mismatch_count(self) -> int:
        return len(self.differences)


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredDashboardComparisonReport:
    id: int
    business_date: date
    primary_source: str
    status: str
    compared_fields: int
    mismatch_count: int
    absolute_tolerance: Decimal
    relative_tolerance: Decimal
    differences: tuple[DashboardDifference, ...]
    sheets_data_as_of: datetime
    db_data_as_of: datetime
    created_at: datetime


class ShadowComparisonRepositoryPort(Protocol):
    async def save(
        self,
        *,
        business_date: date,
        primary_source: str,
        report: DashboardComparisonReport,
    ) -> int: ...

    async def get(self, report_id: int) -> StoredDashboardComparisonReport | None: ...


class DashboardComparisonService:
    def __init__(
        self,
        *,
        absolute_tolerance: Decimal = Decimal("0.01"),
        relative_tolerance: Decimal = Decimal("0.000001"),
    ) -> None:
        if absolute_tolerance < 0 or relative_tolerance < 0:
            raise ValueError("Dashboard comparison tolerances must not be negative")
        self._absolute = absolute_tolerance
        self._relative = relative_tolerance

    def compare(
        self,
        *,
        sheets: MainDashboardSnapshot,
        database: MainDashboardSnapshot,
    ) -> DashboardComparisonReport:
        sheet_values = _financial_values(sheets)
        db_values = _financial_values(database)
        differences: list[DashboardDifference] = []
        compared = 0
        for path in sorted(sheet_values.keys() | db_values.keys()):
            sheet_value = sheet_values.get(path)
            db_value = db_values.get(path)
            if sheet_value is None and db_value is None:
                continue
            if sheet_value is None or db_value is None:
                differences.append(
                    DashboardDifference(
                        path=path,
                        sheet_value=sheet_value,
                        db_value=db_value,
                        absolute_delta=None,
                        relative_delta=None,
                        kind=DifferenceKind.MAPPING,
                    )
                )
                continue
            compared += 1
            delta = abs(db_value - sheet_value)
            scale = max(abs(db_value), abs(sheet_value))
            relative = delta / scale if scale else Decimal(0)
            if delta <= self._absolute or relative <= self._relative:
                continue
            differences.append(
                DashboardDifference(
                    path=path,
                    sheet_value=sheet_value,
                    db_value=db_value,
                    absolute_delta=delta,
                    relative_delta=relative,
                    kind=self._classify(
                    sheets=sheets,
                    database=database,
                    path=path,
                    sheet_value=sheet_value,
                        db_value=db_value,
                        delta=delta,
                    ),
                )
            )
        return DashboardComparisonReport(
            status="matched" if not differences else "mismatched",
            compared_fields=compared,
            differences=tuple(differences),
            absolute_tolerance=self._absolute,
            relative_tolerance=self._relative,
            sheets_data_as_of=sheets.data_as_of,
            db_data_as_of=database.data_as_of,
        )

    def _classify(
        self,
        *,
        sheets: MainDashboardSnapshot,
        database: MainDashboardSnapshot,
        path: str,
        sheet_value: Decimal,
        db_value: Decimal,
        delta: Decimal,
    ) -> DifferenceKind:
        if sheet_value == -db_value and sheet_value != 0:
            return DifferenceKind.SIGN
        if db_value == 0 and sheet_value != 0:
            return DifferenceKind.MISSING_HISTORY
        if delta <= self._absolute * Decimal(10):
            return DifferenceKind.ROUNDING
        if (
            path
            in {
                "reconciliation.client_balances",
                "reconciliation.total_balances",
                "reconciliation.fact_turnover",
                "reconciliation.fact_rub",
                "reconciliation.gap",
            }
            and any("manually maintained" in warning.lower() for warning in sheets.warnings)
        ):
            return DifferenceKind.STALE_EXTERNAL_DATA
        age_delta = abs(sheets.data_as_of - database.data_as_of)
        if age_delta.total_seconds() > 300:
            return DifferenceKind.TIMESTAMP_SKEW
        if any("stale" in warning.lower() for warning in sheets.warnings):
            return DifferenceKind.STALE_EXTERNAL_DATA
        return DifferenceKind.ACCOUNTING_ERROR


def _financial_values(snapshot: MainDashboardSnapshot) -> dict[str, Decimal | None]:
    reconciliation = snapshot.reconciliation
    periods = snapshot.periods
    values = {
        "reconciliation.rub_cash": reconciliation.rub_cash,
        "reconciliation.rub_in_currency": reconciliation.rub_in_currency,
        "reconciliation.total_rub": reconciliation.total_rub,
        "reconciliation.client_balances": reconciliation.client_balances,
        "reconciliation.skyex_balances": reconciliation.skyex_balances,
        "reconciliation.total_balances": reconciliation.total_balances,
        "reconciliation.fact_turnover": reconciliation.fact_turnover,
        "reconciliation.accumulated_profit": reconciliation.accumulated_profit,
        "reconciliation.invested_capital": reconciliation.invested_capital,
        "reconciliation.turnover": reconciliation.turnover,
        "reconciliation.gap": reconciliation.gap,
        "reconciliation.fact_rub": reconciliation.fact_rub,
        "periods.daily_income": periods.daily_income,
        "periods.daily_expense": periods.daily_expense,
        "periods.daily_profit": periods.daily_profit,
        "periods.daily_turnover": periods.daily_turnover,
        "periods.monthly_turnover": periods.monthly_turnover,
        "periods.profitability": periods.profitability,
    }
    for item in snapshot.currencies:
        prefix = f"currencies.{item.code}"
        physical_qty = item.observed_qty if item.observed_qty is not None else item.fact_qty
        values.update(
            {
                f"{prefix}.free_qty": item.free_qty,
                f"{prefix}.internal_rate": item.internal_rate,
                f"{prefix}.rub_cost": item.rub_cost,
                f"{prefix}.physical_qty": physical_qty,
            }
        )
    for item in snapshot.cities:
        prefix = f"cities.{item.city.strip().lower()}"
        values.update(
            {
                f"{prefix}.income": item.income,
                f"{prefix}.expense": item.expense,
                f"{prefix}.profit": item.profit,
            }
        )
    return values
