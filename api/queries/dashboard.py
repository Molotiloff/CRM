from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from api.presentation.dashboard import build_dashboard
from api.rate_providers import (
    DashboardSheetProvider,
    DashboardSheetSnapshot,
    FirmRateProvider,
)
from api.schemas.dashboard import (
    DashboardResponse,
    DashboardShadowComparisonDto,
    DashboardShadowDifferenceDto,
    DashboardShadowReportDto,
)
from services.accounting.dashboard_comparison_service import (
    DashboardComparisonService,
    ShadowComparisonRepositoryPort,
)
from services.accounting.models import (
    MainDashboardCity,
    MainDashboardCurrency,
    MainDashboardOperations,
    MainDashboardPeriods,
    MainDashboardReconciliation,
    MainDashboardSnapshot,
)
from services.accounting.ports import AccountingDashboardReadPort


class DashboardUnavailableError(RuntimeError):
    pass


class DashboardQueryService:
    def __init__(
        self,
        *,
        dashboard_repository: AccountingDashboardReadPort,
        rate_provider: FirmRateProvider,
        sheet_provider: DashboardSheetProvider | None = None,
        source_mode: str = "db_only",
        today_factory: Callable[[], date] | None = None,
        comparison_service: DashboardComparisonService | None = None,
        comparison_repository: ShadowComparisonRepositoryPort | None = None,
    ) -> None:
        self._dashboard = dashboard_repository
        self._rates = rate_provider
        self._sheet = sheet_provider
        self._source_mode = source_mode
        self._today = today_factory or _yekaterinburg_business_date
        self._comparison = comparison_service or DashboardComparisonService()
        self._comparison_repository = comparison_repository

    async def get_dashboard(self) -> DashboardResponse:
        current_date = self._today()
        shadow_summary = None
        if self._source_mode == "sheets":
            if self._sheet is None:
                raise DashboardUnavailableError("Sheets dashboard snapshot provider is unavailable")
            sheet_snapshot = await self._sheet.get_snapshot(today=current_date)
            if sheet_snapshot is None:
                raise DashboardUnavailableError("Sheets dashboard snapshot is unavailable")
            snapshot = _sheet_dashboard_snapshot(sheet_snapshot)
        elif self._source_mode == "db_shadow":
            snapshot, shadow_summary = await self._shadow_dashboard(current_date)
        else:
            try:
                snapshot = await self._dashboard.get_snapshot(business_date=current_date)
            except Exception as exc:
                raise DashboardUnavailableError(
                    "Postgres dashboard snapshot is unavailable"
                ) from exc
            if self._source_mode == "db_primary" and self._sheet is not None:
                try:
                    sheet_snapshot = await self._sheet.get_snapshot(today=current_date)
                except Exception:  # noqa: BLE001 - optional comparison must not affect DB primary
                    sheet_snapshot = None
                if sheet_snapshot is not None:
                    shadow_summary = await self._compare_and_save(
                        current_date,
                        sheets=_sheet_dashboard_snapshot(
                            sheet_snapshot,
                            operational_warning=False,
                        ),
                        database=snapshot,
                        primary_source="postgres",
                    )
        return build_dashboard(
            snapshot=snapshot,
            today=current_date,
            shadow_comparison=shadow_summary,
        )

    async def _shadow_dashboard(
        self, current_date: date
    ) -> tuple[MainDashboardSnapshot, DashboardShadowComparisonDto]:
        if self._sheet is None:
            raise DashboardUnavailableError("Sheets dashboard snapshot provider is unavailable")
        sheet_result, db_result = await asyncio.gather(
            self._sheet.get_snapshot(today=current_date),
            self._dashboard.get_snapshot(business_date=current_date),
            return_exceptions=True,
        )
        if isinstance(sheet_result, BaseException) or sheet_result is None:
            raise DashboardUnavailableError("Sheets dashboard snapshot is unavailable")
        sheet_snapshot = _sheet_dashboard_snapshot(
            sheet_result,
            operational_warning=False,
        )
        if isinstance(db_result, BaseException):
            unavailable = DashboardShadowComparisonDto(
                status="unavailable",
                comparedFields=0,
                mismatchCount=0,
            )
            return (
                MainDashboardSnapshot(
                    source=sheet_snapshot.source,
                    calculated_at=sheet_snapshot.calculated_at,
                    data_as_of=sheet_snapshot.data_as_of,
                    warnings=(*sheet_snapshot.warnings, "Postgres shadow snapshot is unavailable"),
                    currencies=sheet_snapshot.currencies,
                    reconciliation=sheet_snapshot.reconciliation,
                    periods=sheet_snapshot.periods,
                    cities=sheet_snapshot.cities,
                    operations=sheet_snapshot.operations,
                ),
                unavailable,
            )
        summary = await self._compare_and_save(
            current_date,
            sheets=sheet_snapshot,
            database=db_result,
            primary_source="sheets",
        )
        return sheet_snapshot, summary

    async def _compare_and_save(
        self,
        business_date: date,
        *,
        sheets: MainDashboardSnapshot,
        database: MainDashboardSnapshot,
        primary_source: str,
    ) -> DashboardShadowComparisonDto:
        report = self._comparison.compare(sheets=sheets, database=database)
        report_id = None
        if self._comparison_repository is not None:
            try:
                report_id = await self._comparison_repository.save(
                    business_date=business_date,
                    primary_source=primary_source,
                    report=report,
                )
            except Exception:  # noqa: BLE001 - diagnostics must not replace the primary snapshot
                report_id = None
        return DashboardShadowComparisonDto(
            status=report.status,
            comparedFields=report.compared_fields,
            mismatchCount=report.mismatch_count,
            reportId=report_id,
        )

    async def get_rates(self) -> dict[str, float]:
        rates = await self._rates.get_rates()
        return {code: float(rate) for code, rate in rates.items()}

    async def get_shadow_report(self, report_id: int) -> DashboardShadowReportDto | None:
        if self._comparison_repository is None:
            return None
        report = await self._comparison_repository.get(report_id)
        if report is None:
            return None
        return DashboardShadowReportDto(
            id=report.id,
            businessDate=report.business_date,
            primarySource=report.primary_source,
            status=report.status,
            comparedFields=report.compared_fields,
            mismatchCount=report.mismatch_count,
            absoluteTolerance=format(report.absolute_tolerance, "f"),
            relativeTolerance=format(report.relative_tolerance, "f"),
            sheetsDataAsOf=report.sheets_data_as_of,
            dbDataAsOf=report.db_data_as_of,
            createdAt=report.created_at,
            differences=[
                DashboardShadowDifferenceDto(
                    path=item.path,
                    sheet=_optional_decimal_string(item.sheet_value),
                    database=_optional_decimal_string(item.db_value),
                    absoluteDelta=_optional_decimal_string(item.absolute_delta),
                    relativeDelta=_optional_decimal_string(item.relative_delta),
                    classification=item.kind.value,
                )
                for item in report.differences
            ],
        )


def _sheet_dashboard_snapshot(
    sheet: DashboardSheetSnapshot,
    *,
    operational_warning: bool = True,
) -> MainDashboardSnapshot:
    calculated_at = datetime.now(UTC)
    invested_capital = (
        sheet.invested_capital
        if sheet.invested_capital is not None
        else sheet.turnover - sheet.profit
    )
    daily_income = (
        sheet.daily_profit
        if sheet.daily_profit is not None
        else sheet.income
    )
    currencies = tuple(
        MainDashboardCurrency(
            code=code,
            free_qty=value.amount,
            internal_rate=value.rate,
            rub_cost=value.rub_value,
            client_qty=value.client_amount,
            deal_profit_qty=Decimal(0),
            fact_qty=value.fact_amount,
            observed_qty=None,
            gap=None,
            observed_at=None,
        )
        for code, value in sheet.currencies.items()
        if code in {"EUR", "USDT", "USD_BL", "USD_WH"}
    )
    return MainDashboardSnapshot(
        source="sheets",
        calculated_at=calculated_at,
        data_as_of=calculated_at,
        warnings=(
            (
                "Client balances and FX client amounts are manually maintained in Sheets",
                "Operational PostgreSQL metrics are unavailable in sheets mode",
            )
            if operational_warning
            else (
                "Client balances and FX client amounts are manually maintained in Sheets",
            )
        ),
        currencies=currencies,
        reconciliation=MainDashboardReconciliation(
            rub_cash=sheet.rub_cash,
            rub_in_currency=sheet.rub_in_currency,
            total_rub=sheet.total_rub,
            client_balances=sheet.client_balances,
            skyex_balances=sheet.skyex_balances,
            total_balances=sheet.total_balances,
            fact_turnover=sheet.fact_turnover,
            accumulated_profit=sheet.profit,
            invested_capital=invested_capital,
            turnover=invested_capital + sheet.profit,
            gap=sheet.gap,
            fact_rub=sheet.fact_rub,
        ),
        periods=MainDashboardPeriods(
            daily_income=daily_income,
            daily_expense=sheet.today_expense,
            daily_profit=daily_income - sheet.today_expense,
            daily_turnover=sheet.turnover,
            monthly_turnover=sheet.monthly_turnover,
            profitability=sheet.profitability,
        ),
        cities=tuple(
            MainDashboardCity(
                city=city,
                income=value.income,
                expense=value.expense,
                profit=value.profit,
                active_requests=0,
            )
            for city, value in sheet.cities.items()
        ),
        operations=MainDashboardOperations(
            active_requests=None,
            clients_with_balance=None,
            queued_usdt_qty=Decimal(0),
            queue_shortage_qty=Decimal(0),
            onchain_liquid_qty=None,
            profit_in_transit_qty=Decimal(0),
        ),
    )


def _optional_decimal_string(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _yekaterinburg_business_date() -> date:
    return datetime.now(ZoneInfo("Asia/Yekaterinburg")).date()
