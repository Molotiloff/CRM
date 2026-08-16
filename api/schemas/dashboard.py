from __future__ import annotations

from pydantic import BaseModel


class DashboardTopMetricDto(BaseModel):
    id: str
    title: str
    value: str | int | float
    subtitleLabel: str | None = None
    subtitleValue: str | int | float | None = None
    tone: str
    icon: str


class DashboardDailyIndicatorDto(BaseModel):
    id: str
    label: str
    value: str | int | float
    tone: str


class DashboardCurrencyFactDto(BaseModel):
    code: str
    label: str
    amount: float
    rate: float
    rubValue: float
    clientAmount: float
    factAmount: float
    tone: str


class DashboardFinanceIndicatorDto(BaseModel):
    id: str
    label: str
    value: str | int | float
    percent: str | None = None
    isNegative: bool | None = None


class DashboardCitySummaryRowDto(BaseModel):
    label: str
    value: str | int | float
    tone: str | None = None


class DashboardCitySummaryDto(BaseModel):
    id: str
    city: str
    rows: list[DashboardCitySummaryRowDto]


class DashboardSystemMetricDto(BaseModel):
    id: str
    title: str
    value: str | int | float
    subtitle: str
    tone: str


class DashboardResponse(BaseModel):
    dateLabel: str
    weekdayLabel: str
    cities: list[str]
    topMetrics: list[DashboardTopMetricDto]
    dailyIndicators: list[DashboardDailyIndicatorDto]
    currencies: list[DashboardCurrencyFactDto]
    finance: list[DashboardFinanceIndicatorDto]
    citySummaries: list[DashboardCitySummaryDto]
    systemMetrics: list[DashboardSystemMetricDto]
    lastUpdatedLabel: str
