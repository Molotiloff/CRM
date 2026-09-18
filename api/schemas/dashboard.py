from __future__ import annotations

from datetime import date, datetime

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
    amount: str
    rate: str
    rubValue: str
    clientAmount: str
    dealProfitAmount: str
    factAmount: str
    observedAmount: str | None
    gap: str | None
    observedAt: datetime | None
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


class DashboardShadowComparisonDto(BaseModel):
    status: str
    comparedFields: int
    mismatchCount: int
    reportId: int | None = None


class DashboardShadowDifferenceDto(BaseModel):
    path: str
    sheet: str | None
    database: str | None
    absoluteDelta: str | None
    relativeDelta: str | None
    classification: str


class DashboardShadowReportDto(BaseModel):
    id: int
    businessDate: date
    primarySource: str
    status: str
    comparedFields: int
    mismatchCount: int
    absoluteTolerance: str
    relativeTolerance: str
    sheetsDataAsOf: datetime
    dbDataAsOf: datetime
    createdAt: datetime
    differences: list[DashboardShadowDifferenceDto]


class DashboardResponse(BaseModel):
    source: str
    calculatedAt: datetime
    dataAsOf: datetime
    warnings: list[str]
    shadowComparison: DashboardShadowComparisonDto | None = None
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
