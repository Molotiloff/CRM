from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

from api.presentation.dashboard import build_dashboard
from api.rate_providers import (
    DashboardSheetProvider,
    DashboardSheetSnapshot,
    FirmRateProvider,
)
from api.schemas.dashboard import DashboardResponse
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
        today_factory: Callable[[], date] = date.today,
    ) -> None:
        self._dashboard = dashboard_repository
        self._rates = rate_provider
        self._sheet = sheet_provider
        self._source_mode = source_mode
        self._today = today_factory

    async def get_dashboard(self) -> DashboardResponse:
        current_date = self._today()
        if self._source_mode in {"sheets", "db_shadow"}:
            if self._sheet is None:
                raise DashboardUnavailableError("Sheets dashboard snapshot provider is unavailable")
            sheet_snapshot = await self._sheet.get_snapshot(today=current_date)
            if sheet_snapshot is None:
                raise DashboardUnavailableError("Sheets dashboard snapshot is unavailable")
            snapshot = _sheet_dashboard_snapshot(sheet_snapshot)
        else:
            try:
                snapshot = await self._dashboard.get_snapshot(business_date=current_date)
            except Exception as exc:
                raise DashboardUnavailableError(
                    "Postgres dashboard snapshot is unavailable"
                ) from exc
        return build_dashboard(snapshot=snapshot, today=current_date)

    async def get_rates(self) -> dict[str, float]:
        rates = await self._rates.get_rates()
        return {code: float(rate) for code, rate in rates.items()}


def _sheet_dashboard_snapshot(sheet: DashboardSheetSnapshot) -> MainDashboardSnapshot:
    calculated_at = datetime.now(UTC)
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
        warnings=("Operational PostgreSQL metrics are unavailable in sheets mode",),
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
            invested_capital=sheet.turnover - sheet.profit,
            turnover=sheet.turnover,
            gap=sheet.gap,
            fact_rub=sheet.fact_rub,
        ),
        periods=MainDashboardPeriods(
            daily_income=sheet.income,
            daily_expense=sheet.today_expense,
            daily_profit=sheet.income - sheet.today_expense,
            daily_turnover=Decimal(0),
            monthly_turnover=Decimal(0),
            profitability=None,
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
