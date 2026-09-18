from __future__ import annotations

from datetime import date
from decimal import Decimal

from api.schemas.dashboard import (
    DashboardCitySummaryDto,
    DashboardCitySummaryRowDto,
    DashboardCurrencyFactDto,
    DashboardDailyIndicatorDto,
    DashboardFinanceIndicatorDto,
    DashboardResponse,
    DashboardShadowComparisonDto,
    DashboardSystemMetricDto,
    DashboardTopMetricDto,
)
from services.accounting.models import MainDashboardSnapshot


def build_dashboard(
    *,
    snapshot: MainDashboardSnapshot,
    today: date,
    shadow_comparison: DashboardShadowComparisonDto | None = None,
) -> DashboardResponse:
    reconciliation = snapshot.reconciliation
    periods = snapshot.periods
    operations = snapshot.operations
    return DashboardResponse(
        source=snapshot.source,
        calculatedAt=snapshot.calculated_at,
        dataAsOf=snapshot.data_as_of,
        warnings=list(snapshot.warnings),
        shadowComparison=shadow_comparison,
        dateLabel=today.strftime("%d.%m.%Y"),
        weekdayLabel=_weekday_label(today),
        cities=["Все города", *(_city_title(item.city) for item in snapshot.cities)],
        topMetrics=[
            DashboardTopMetricDto(
                id="fact-turnover",
                title="Факт. оборот",
                value=_format_rub(reconciliation.fact_turnover),
                subtitleLabel="Общие балансы",
                subtitleValue=_format_rub(reconciliation.total_balances),
                tone="blue",
                icon="reports",
            ),
            DashboardTopMetricDto(
                id="total-rub",
                title="Общий RUB",
                value=_format_rub(reconciliation.total_rub),
                subtitleLabel="RUB в валюте",
                subtitleValue=_format_rub(reconciliation.rub_in_currency),
                tone="green" if reconciliation.total_rub >= 0 else "red",
                icon="coins",
            ),
            DashboardTopMetricDto(
                id="active-requests",
                title="Активные заявки",
                value=(operations.active_requests if operations.active_requests is not None else "—"),
                subtitleLabel="Все очереди",
                subtitleValue="сейчас",
                tone="orange",
                icon="deals",
            ),
            DashboardTopMetricDto(
                id="expense-today",
                title="Расход сегодня",
                value=_format_rub(periods.daily_expense),
                subtitleLabel="Расходы",
                subtitleValue=today.strftime("%d.%m.%Y"),
                tone="red" if periods.daily_expense else "neutral",
                icon="expenses",
            ),
        ],
        dailyIndicators=[
            DashboardDailyIndicatorDto(
                id="income", label="Доход", value=_format_rub(periods.daily_income), tone="green"
            ),
            DashboardDailyIndicatorDto(
                id="expense", label="Расход", value=_format_rub(periods.daily_expense), tone="red"
            ),
            DashboardDailyIndicatorDto(
                id="profit",
                label="Общая прибыль",
                value=_format_rub(periods.daily_profit),
                tone=_tone(periods.daily_profit),
            ),
            DashboardDailyIndicatorDto(
                id="turnover",
                label="Оборот за день",
                value=_format_rub(periods.daily_turnover),
                tone="blue",
            ),
            DashboardDailyIndicatorDto(
                id="gap",
                label="Разрыв",
                value=_format_rub(reconciliation.gap),
                tone=_tone(reconciliation.gap),
            ),
        ],
        currencies=[
            DashboardCurrencyFactDto(
                code=item.code,
                label=item.code.replace("_", " "),
                amount=_decimal_string(item.free_qty),
                rate=_decimal_string(item.internal_rate),
                rubValue=_decimal_string(item.rub_cost),
                clientAmount=_decimal_string(item.client_qty),
                dealProfitAmount=_decimal_string(item.deal_profit_qty),
                factAmount=_decimal_string(item.fact_qty),
                observedAmount=(
                    _decimal_string(item.observed_qty)
                    if item.observed_qty is not None
                    else None
                ),
                gap=_decimal_string(item.gap) if item.gap is not None else None,
                observedAt=item.observed_at,
                tone=_currency_tone(item.code),
            )
            for item in snapshot.currencies
        ],
        finance=[
            DashboardFinanceIndicatorDto(
                id="rub-cash",
                label="RUB в кассах",
                value=_format_rub(reconciliation.rub_cash),
            ),
            DashboardFinanceIndicatorDto(
                id="rub-in-currency",
                label="RUB в валюте",
                value=_format_rub(reconciliation.rub_in_currency),
            ),
            DashboardFinanceIndicatorDto(
                id="total-rub", label="Общий RUB", value=_format_rub(reconciliation.total_rub)
            ),
            DashboardFinanceIndicatorDto(
                id="client-balances",
                label="Балансы клиентов",
                value=_format_rub(reconciliation.client_balances),
                isNegative=reconciliation.client_balances < 0,
            ),
            DashboardFinanceIndicatorDto(
                id="skyex-balances",
                label="Балансы SkyEx",
                value=_format_rub(reconciliation.skyex_balances),
                isNegative=reconciliation.skyex_balances < 0,
            ),
            DashboardFinanceIndicatorDto(
                id="total-balances",
                label="Общие балансы",
                value=_format_rub(reconciliation.total_balances),
                isNegative=reconciliation.total_balances < 0,
            ),
            DashboardFinanceIndicatorDto(
                id="fact-turnover",
                label="Фактический оборот",
                value=_format_rub(reconciliation.fact_turnover),
            ),
            DashboardFinanceIndicatorDto(
                id="invested-capital",
                label="Вложенный капитал",
                value=_format_rub(reconciliation.invested_capital),
            ),
            DashboardFinanceIndicatorDto(
                id="accumulated-profit",
                label="Накопленная прибыль",
                value=_format_rub(reconciliation.accumulated_profit),
                isNegative=reconciliation.accumulated_profit < 0,
            ),
            DashboardFinanceIndicatorDto(
                id="turnover",
                label="Расчётный оборот",
                value=_format_rub(reconciliation.turnover),
            ),
            DashboardFinanceIndicatorDto(
                id="gap",
                label="Разрыв",
                value=_format_rub(reconciliation.gap),
                isNegative=reconciliation.gap < 0,
            ),
            DashboardFinanceIndicatorDto(
                id="fact-rub", label="Факт RUB", value=_format_rub(reconciliation.fact_rub)
            ),
        ],
        citySummaries=[
            DashboardCitySummaryDto(
                id=_city_id(item.city),
                city=_city_label(item.city),
                rows=[
                    DashboardCitySummaryRowDto(
                        label="Активных заявок", value=item.active_requests, tone="blue"
                    ),
                    DashboardCitySummaryRowDto(
                        label="Доход", value=_format_rub(item.income), tone="green"
                    ),
                    DashboardCitySummaryRowDto(
                        label="Расход", value=_format_rub(item.expense), tone="red"
                    ),
                    DashboardCitySummaryRowDto(
                        label="Прибыль", value=_format_rub(item.profit), tone=_tone(item.profit)
                    ),
                ],
            )
            for item in snapshot.cities
        ],
        systemMetrics=[
            DashboardSystemMetricDto(
                id="clients-with-balance",
                title="Клиентов с балансом",
                value=(
                    operations.clients_with_balance
                    if operations.clients_with_balance is not None
                    else "—"
                ),
                subtitle="ненулевые некассовые счета",
                tone="green",
            ),
            DashboardSystemMetricDto(
                id="queued-usdt",
                title="USDT в очереди",
                value=_decimal_string(operations.queued_usdt_qty),
                subtitle="единая очередь исполнения",
                tone="blue",
            ),
            DashboardSystemMetricDto(
                id="queue-shortage-usdt",
                title="USDT на откупе",
                value=_decimal_string(operations.queue_shortage_qty),
                subtitle="дефицит очереди",
                tone="orange" if operations.queue_shortage_qty else "green",
            ),
            DashboardSystemMetricDto(
                id="onchain-usdt",
                title="USDT on-chain",
                value=(
                    _decimal_string(operations.onchain_liquid_qty)
                    if operations.onchain_liquid_qty is not None
                    else "—"
                ),
                subtitle="последний физический снимок",
                tone="green" if operations.onchain_liquid_qty is not None else "orange",
            ),
            DashboardSystemMetricDto(
                id="profit-in-transit",
                title="USDT в пути",
                value=_decimal_string(operations.profit_in_transit_qty),
                subtitle="начисленная прибыль",
                tone="orange" if operations.profit_in_transit_qty else "neutral",
            ),
        ],
        lastUpdatedLabel=(
            f"Postgres snapshot: {snapshot.data_as_of.strftime('%d.%m.%Y %H:%M UTC')}"
        ),
    )


def _decimal_string(value: Decimal) -> str:
    return format(value, "f")


def _tone(value: Decimal) -> str:
    if value > 0:
        return "green"
    if value < 0:
        return "red"
    return "neutral"


def _format_rub(value: Decimal) -> str:
    rounded = value.quantize(Decimal("1"))
    return f"{rounded:,.0f} ₽".replace(",", " ")


def _weekday_label(value: date) -> str:
    return (
        "Понедельник",
        "Вторник",
        "Среда",
        "Четверг",
        "Пятница",
        "Суббота",
        "Воскресенье",
    )[value.weekday()]


def _currency_tone(code: str) -> str:
    return {
        "EUR": "blue",
        "USDT": "green",
        "USD_WH": "orange",
        "USD_BL": "purple",
    }.get(code, "neutral")


def _city_id(city: str) -> str:
    normalized = city.strip().lower()
    return {
        "екб": "ekb",
        "екатеринбург": "ekb",
        "члб": "chlb",
        "челябинск": "chlb",
        "мск": "msk",
        "москва": "msk",
        "тюм": "tmn",
        "тюмень": "tmn",
    }.get(normalized, normalized)


def _city_label(city: str) -> str:
    return {
        "ekb": "ЕКБ",
        "chlb": "ЧЛБ",
        "msk": "МСК",
        "tmn": "ТЮМ",
    }.get(_city_id(city), city.strip().upper())


def _city_title(city: str) -> str:
    return _city_label(city)
