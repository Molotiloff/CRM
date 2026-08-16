from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from api.rate_providers import DashboardSheetSnapshot
from api.schemas.dashboard import (
    DashboardCitySummaryDto,
    DashboardCitySummaryRowDto,
    DashboardCurrencyFactDto,
    DashboardDailyIndicatorDto,
    DashboardFinanceIndicatorDto,
    DashboardResponse,
    DashboardSystemMetricDto,
    DashboardTopMetricDto,
)


def build_dashboard(
    *,
    balance_rows: list[dict[str, Any]],
    cash_desks: list[dict[str, Any]],
    active_schedule_counts: list[dict[str, Any]],
    active_exchange_requests: int,
    rub_rates: Mapping[str, Decimal],
    today_expenses: Decimal,
    sheet_snapshot: DashboardSheetSnapshot | None = None,
    today: date | None = None,
) -> DashboardResponse:
    current_date = today or date.today()
    client_by_currency: dict[str, Decimal] = {}
    client_rub_total = Decimal(0)
    for row in balance_rows:
        code = str(row["currency_code"]).upper()
        amount = _dec(row["balance"])
        client_by_currency[code] = client_by_currency.get(code, Decimal(0)) + amount
        client_rub_total += amount * rub_rates.get(code, Decimal(0))

    cash_by_currency: dict[str, Decimal] = {}
    for row in cash_desks:
        code = str(row["currency_code"]).upper()
        amount = _dec(row["balance"])
        cash_by_currency[code] = cash_by_currency.get(code, Decimal(0)) + amount

    rub_cash = cash_by_currency.get("RUB", Decimal(0))
    rub_in_currency = sum(
        amount * rub_rates.get(code, Decimal(0))
        for code, amount in cash_by_currency.items()
        if code != "RUB"
    )
    total_rub = rub_cash + rub_in_currency
    fact_turnover = total_rub - client_rub_total
    active_requests = active_exchange_requests + sum(
        int(row["active_count"]) for row in active_schedule_counts
    )
    values = _dashboard_values(
        sheet_snapshot=sheet_snapshot,
        fact_turnover=fact_turnover,
        total_rub=total_rub,
        client_rub_total=client_rub_total,
        rub_cash=rub_cash,
        rub_in_currency=rub_in_currency,
        today_expenses=today_expenses,
    )

    return DashboardResponse(
        dateLabel=current_date.strftime("%d.%m.%Y"),
        weekdayLabel=_weekday_label(current_date),
        cities=[
            "Все города",
            *dict.fromkeys(_city_title(str(row["city"])) for row in active_schedule_counts),
        ],
        topMetrics=[
            DashboardTopMetricDto(
                id="fact-turnover",
                title="Факт. оборот",
                value=_format_rub(values["fact_turnover"]),
                subtitleLabel="Клиентские балансы",
                subtitleValue=_format_rub(values["client_balances"]),
                tone="blue",
                icon="reports",
            ),
            DashboardTopMetricDto(
                id="total-rub",
                title="Общий RUB",
                value=_format_rub(values["total_rub"]),
                subtitleLabel="RUB в валюте",
                subtitleValue=_format_rub(values["rub_in_currency"]),
                tone="green" if values["total_rub"] >= 0 else "red",
                icon="coins",
            ),
            DashboardTopMetricDto(
                id="active-requests",
                title="Активные заявки",
                value=active_requests,
                subtitleLabel="Расписание",
                subtitleValue="сейчас",
                tone="orange",
                icon="deals",
            ),
            DashboardTopMetricDto(
                id="expense-today",
                title="Расход сегодня",
                value=_format_rub(values["today_expense"]),
                subtitleLabel="Расходы",
                subtitleValue=current_date.strftime("%d.%m.%Y"),
                tone="red" if values["today_expense"] else "neutral",
                icon="expenses",
            ),
        ],
        dailyIndicators=[
            DashboardDailyIndicatorDto(
                id="income", label="Доход", value=_format_rub(values["income"]), tone="green"
            ),
            DashboardDailyIndicatorDto(
                id="expense", label="Расход", value=_format_rub(values["expense"]), tone="red"
            ),
            DashboardDailyIndicatorDto(
                id="profit",
                label="Общая прибыль",
                value=_format_rub(values["profit"]),
                tone=_tone(values["profit"]),
            ),
            DashboardDailyIndicatorDto(
                id="turnover",
                label="Оборот за день",
                value=_format_rub(values["turnover"]),
                tone="blue",
            ),
            DashboardDailyIndicatorDto(
                id="gap",
                label="Разрыв",
                value=_format_rub(values["gap"]),
                tone=_tone(values["gap"]),
            ),
        ],
        currencies=[
            _dashboard_currency_fact_from_sources(
                code=code,
                sheet_snapshot=sheet_snapshot,
                cash_by_currency=cash_by_currency,
                client_by_currency=client_by_currency,
                rub_rates=rub_rates,
            )
            for code in ("EUR", "USDT", "USD_WH", "USD_BL", "CNY")
        ],
        finance=[
            DashboardFinanceIndicatorDto(
                id="total-rub", label="Общий RUB", value=_format_rub(values["total_rub"])
            ),
            DashboardFinanceIndicatorDto(
                id="total-balances",
                label="Общий балансы",
                value=_format_rub(values["total_balances"]),
                isNegative=values["total_balances"] < 0,
            ),
            DashboardFinanceIndicatorDto(
                id="fact-rub",
                label="Факт RUB",
                value=_format_rub(values["fact_rub"]),
            ),
            DashboardFinanceIndicatorDto(
                id="rub-in-currency",
                label="RUB в валюте",
                value=_format_rub(values["rub_in_currency"]),
            ),
            DashboardFinanceIndicatorDto(
                id="client-balances",
                label="Балансы клиентов",
                value=_format_rub(values["client_balances"]),
                isNegative=values["client_balances"] < 0,
            ),
            DashboardFinanceIndicatorDto(
                id="skyex-balances",
                label="Балансы SkyEx",
                value=_format_rub(values["skyex_balances"]),
            ),
        ],
        citySummaries=_dashboard_city_summaries(active_schedule_counts, sheet_snapshot),
        systemMetrics=[
            DashboardSystemMetricDto(
                id="clients-with-balance",
                title="Клиентов с балансом",
                value=len({int(row["client_id"]) for row in balance_rows}),
                subtitle="ненулевые счета",
                tone="green",
            ),
            DashboardSystemMetricDto(
                id="active-requests",
                title="Активные заявки",
                value=active_requests,
                subtitle="request_schedule_entries",
                tone="blue",
            ),
            DashboardSystemMetricDto(
                id="rub-rate-sources",
                title="Курсов в БД",
                value=len(rub_rates),
                subtitle="exchange_request_links",
                tone="orange",
            ),
        ],
        lastUpdatedLabel=(
            f"Google Sheets + Postgres: {datetime.now(UTC).strftime('%d.%m.%Y %H:%M UTC')}"
            if sheet_snapshot
            else f"Postgres snapshot: {datetime.now(UTC).strftime('%d.%m.%Y %H:%M UTC')}"
        ),
    )


def _tone(value: Decimal) -> str:
    if value > 0:
        return "green"
    if value < 0:
        return "red"
    return "neutral"


def _dashboard_currency_fact(
    *,
    code: str,
    cash_amount: Decimal,
    client_amount: Decimal,
    rub_rate: Decimal,
) -> DashboardCurrencyFactDto:
    fact_amount = cash_amount + client_amount
    return DashboardCurrencyFactDto(
        code=code,
        label=code.replace("_", " "),
        amount=_float(cash_amount),
        rate=_float(rub_rate),
        rubValue=_float(cash_amount * rub_rate),
        clientAmount=_float(client_amount),
        factAmount=_float(fact_amount),
        tone=_currency_tone(code),
    )


def _dashboard_currency_fact_from_sources(
    *,
    code: str,
    sheet_snapshot: DashboardSheetSnapshot | None,
    cash_by_currency: Mapping[str, Decimal],
    client_by_currency: Mapping[str, Decimal],
    rub_rates: Mapping[str, Decimal],
) -> DashboardCurrencyFactDto:
    if sheet_snapshot and code in sheet_snapshot.currencies:
        value = sheet_snapshot.currencies[code]
        return DashboardCurrencyFactDto(
            code=code,
            label=code.replace("_", " "),
            amount=_float(value.amount),
            rate=_float(value.rate),
            rubValue=_float(value.rub_value),
            clientAmount=_float(value.client_amount),
            factAmount=_float(value.fact_amount),
            tone=_currency_tone(code),
        )
    return _dashboard_currency_fact(
        code=code,
        cash_amount=_dashboard_amount(cash_by_currency, code),
        client_amount=_dashboard_amount(client_by_currency, code),
        rub_rate=_dashboard_rate(rub_rates, code),
    )


def _dashboard_values(
    *,
    sheet_snapshot: DashboardSheetSnapshot | None,
    fact_turnover: Decimal,
    total_rub: Decimal,
    client_rub_total: Decimal,
    rub_cash: Decimal,
    rub_in_currency: Decimal,
    today_expenses: Decimal,
) -> dict[str, Decimal]:
    if sheet_snapshot:
        return {
            "income": sheet_snapshot.income,
            "expense": sheet_snapshot.expense,
            "profit": sheet_snapshot.profit,
            "turnover": sheet_snapshot.turnover,
            "gap": sheet_snapshot.gap,
            "today_expense": sheet_snapshot.today_expense,
            "fact_turnover": sheet_snapshot.fact_turnover,
            "total_rub": sheet_snapshot.total_rub,
            "total_balances": sheet_snapshot.total_balances,
            "fact_rub": sheet_snapshot.fact_rub,
            "rub_in_currency": sheet_snapshot.rub_in_currency,
            "client_balances": sheet_snapshot.client_balances,
            "skyex_balances": sheet_snapshot.skyex_balances,
        }
    return {
        "income": Decimal(0),
        "expense": today_expenses,
        "profit": Decimal(0),
        "turnover": fact_turnover,
        "gap": Decimal(0),
        "today_expense": today_expenses,
        "fact_turnover": fact_turnover,
        "total_rub": total_rub,
        "total_balances": client_rub_total,
        "fact_rub": total_rub + client_rub_total,
        "rub_in_currency": rub_in_currency,
        "client_balances": client_rub_total,
        "skyex_balances": rub_cash,
    }


def _dashboard_city_summaries(
    active_schedule_counts: list[dict[str, Any]],
    sheet_snapshot: DashboardSheetSnapshot | None,
) -> list[DashboardCitySummaryDto]:
    active_by_city = {
        _city_id(str(row["city"])): int(row["active_count"]) for row in active_schedule_counts
    }
    sheet_by_city = {
        _city_id(city): value
        for city, value in (sheet_snapshot.cities.items() if sheet_snapshot else ())
    }
    city_ids = list(active_by_city)
    city_ids.extend(city_id for city_id in sheet_by_city if city_id not in active_by_city)

    summaries: list[DashboardCitySummaryDto] = []
    for city_id in city_ids:
        rows = [
            DashboardCitySummaryRowDto(
                label="Активных заявок",
                value=active_by_city.get(city_id, 0),
                tone="blue",
            )
        ]
        if city_id in sheet_by_city:
            financial = sheet_by_city[city_id]
            rows.extend(
                (
                    DashboardCitySummaryRowDto(
                        label="Доход",
                        value=_format_rub(financial.income),
                        tone="green",
                    ),
                    DashboardCitySummaryRowDto(
                        label="Расход",
                        value=_format_rub(financial.expense),
                        tone="red",
                    ),
                    DashboardCitySummaryRowDto(
                        label="Прибыль",
                        value=_format_rub(financial.profit),
                        tone=_tone(financial.profit),
                    ),
                )
            )
        summaries.append(
            DashboardCitySummaryDto(
                id=city_id,
                city=_city_label(city_id),
                rows=rows,
            )
        )
    return summaries


def _dashboard_amount(values: Mapping[str, Decimal], code: str) -> Decimal:
    aliases = {
        "USD_WH": ("USD_WH", "USDW", "USD"),
        "USD_BL": ("USD_BL", "USDB"),
    }.get(code, (code,))
    return sum((values.get(alias, Decimal(0)) for alias in aliases), Decimal(0))


def _dashboard_rate(values: Mapping[str, Decimal], code: str) -> Decimal:
    aliases = {
        "USD_WH": ("USD_WH", "USDW", "USD"),
        "USD_BL": ("USD_BL", "USDB", "USD"),
    }.get(code, (code,))
    return next((values[alias] for alias in aliases if alias in values), Decimal(0))


def _format_rub(value: Any) -> str:
    amount = _dec(value)
    rounded = amount.quantize(Decimal("1"))
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
        "CNY": "neutral",
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
    }.get(normalized, normalized)


def _city_label(city: str) -> str:
    return {
        "ekb": "ЕКБ",
        "chlb": "ЧЛБ",
        "msk": "МСК",
    }.get(_city_id(city), city.strip().upper())


def _city_title(city: str) -> str:
    return {
        "ekb": "Екатеринбург",
        "chlb": "Челябинск",
        "msk": "Москва",
    }.get(_city_id(city), city.strip().title())


def _float(value: Any) -> float:
    return float(_dec(value))


def _dec(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)
