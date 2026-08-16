from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

from api.presentation.dashboard import build_dashboard
from api.rate_providers import DashboardSheetProvider, FirmRateProvider
from api.read_repositories import BalanceReadRepository, DashboardReadRepository
from api.schemas.dashboard import DashboardResponse


class DashboardQueryService:
    def __init__(
        self,
        *,
        balance_repository: BalanceReadRepository,
        dashboard_repository: DashboardReadRepository,
        rate_provider: FirmRateProvider,
        sheet_provider: DashboardSheetProvider,
        today_factory: Callable[[], date] = date.today,
    ) -> None:
        self._balances = balance_repository
        self._dashboard = dashboard_repository
        self._rates = rate_provider
        self._sheet = sheet_provider
        self._today = today_factory

    async def get_dashboard(self) -> DashboardResponse:
        current_date = self._today()
        balance_rows = await self._balances.nonzero_balances()
        cash_desks = await self._dashboard.cash_desks_balances()
        active_schedule_counts = await self._dashboard.active_schedule_counts()
        active_exchange_requests = await self._dashboard.active_exchange_request_count()
        sheet_snapshot = await self._sheet.get_snapshot(today=current_date)
        rub_rates = await self._combined_rates(
            sheet_rates=sheet_snapshot.rates if sheet_snapshot else None
        )
        today_expenses = await self._dashboard.today_expenses_total()
        return build_dashboard(
            balance_rows=balance_rows,
            cash_desks=cash_desks,
            active_schedule_counts=active_schedule_counts,
            active_exchange_requests=active_exchange_requests,
            rub_rates=rub_rates,
            today_expenses=today_expenses,
            sheet_snapshot=sheet_snapshot,
            today=current_date,
        )

    async def get_rates(self) -> dict[str, float]:
        rates = await self._combined_rates()
        return {code: float(rate) for code, rate in rates.items()}

    async def _combined_rates(
        self,
        *,
        sheet_rates: dict[str, Decimal] | None = None,
    ) -> dict[str, Decimal]:
        database_rates = await self._balances.latest_rub_rates()
        current_sheet_rates = sheet_rates or await self._rates.get_rates()
        return database_rates | current_sheet_rates
