from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

log = logging.getLogger(__name__)


class FirmRateProvider(Protocol):
    async def get_rates(self) -> dict[str, Decimal]: ...


@dataclass(frozen=True, slots=True)
class DashboardCurrencySnapshot:
    amount: Decimal
    rate: Decimal
    rub_value: Decimal
    client_amount: Decimal
    fact_amount: Decimal


@dataclass(frozen=True, slots=True)
class DashboardCityFinancialSnapshot:
    income: Decimal
    expense: Decimal
    profit: Decimal


@dataclass(frozen=True, slots=True)
class DashboardSheetSnapshot:
    income: Decimal
    expense: Decimal
    profit: Decimal
    turnover: Decimal
    gap: Decimal
    today_expense: Decimal
    fact_turnover: Decimal
    total_rub: Decimal
    total_balances: Decimal
    fact_rub: Decimal
    rub_cash: Decimal
    rub_in_currency: Decimal
    client_balances: Decimal
    skyex_balances: Decimal
    currencies: dict[str, DashboardCurrencySnapshot]
    cities: dict[str, DashboardCityFinancialSnapshot]

    @property
    def rates(self) -> dict[str, Decimal]:
        return {
            "RUB": Decimal("1"),
            **{code: value.rate for code, value in self.currencies.items()},
        }


class DashboardSheetProvider(Protocol):
    async def get_snapshot(self, *, today: date) -> DashboardSheetSnapshot | None: ...


class DashboardSheetsGateway(Protocol):
    async def read_main_rate(
        self,
        code: str,
        cell_map: dict[str, str] | None = None,
    ) -> Decimal: ...

    async def read_sheet_ranges(
        self,
        ranges: list[str],
    ) -> dict[str, list[list[str]]]: ...


class GutilsFirmRateProvider:
    _sheet_codes = ("EUR", "USDT", "USD", "USDW")
    _crm_codes = {"USD": "USD_BL", "USDW": "USD_WH"}

    def __init__(self, gateway: DashboardSheetsGateway) -> None:
        self._gateway = gateway
        self._lock = asyncio.Lock()

    async def get_rates(self) -> dict[str, Decimal]:
        async with self._lock:
            results = await self._read_rates()

        rates: dict[str, Decimal] = {"RUB": Decimal("1")}
        for code, result in zip(self._sheet_codes, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("Could not read %s firm rate from Google Sheets: %s", code, result)
                continue
            rates[self._crm_codes.get(code, code)] = result
        return rates

    async def get_snapshot(self, *, today: date) -> DashboardSheetSnapshot | None:
        async with self._lock:
            try:
                values = await self._gateway.read_sheet_ranges(
                    [
                        "Главная!A1:N14",
                        "Расходы!B3:C1000",
                        "Расходы!H3:I1000",
                    ],
                )
            except Exception as exc:  # noqa: BLE001 - dashboard must survive an external outage
                log.warning("Could not read dashboard snapshot from Google Sheets: %s", exc)
                return None

        main = values.get("Главная!A1:N14", [])
        try:
            return DashboardSheetSnapshot(
                income=_cell_decimal(main, 8, 2),
                expense=_cell_decimal(main, 9, 2),
                profit=_cell_decimal(main, 10, 2),
                turnover=_cell_decimal(main, 14, 2),
                gap=_cell_decimal(main, 11, 2),
                today_expense=_expenses_for_date(values, today),
                fact_turnover=_cell_decimal(main, 1, 11),
                total_rub=_cell_decimal(main, 3, 11),
                total_balances=_cell_decimal(main, 4, 11),
                fact_rub=_cell_decimal(main, 5, 11),
                client_balances=_cell_decimal(main, 7, 11),
                skyex_balances=_cell_decimal(main, 8, 11),
                rub_cash=_cell_decimal(main, 10, 11),
                rub_in_currency=_cell_decimal(main, 11, 11),
                currencies={
                    "EUR": _currency_snapshot(main, start_row=1, column=5),
                    "USDT": _currency_snapshot(main, start_row=7, column=5),
                    "USD_WH": _currency_snapshot(main, start_row=1, column=8),
                    "USD_BL": _currency_snapshot(main, start_row=7, column=8),
                },
                cities={
                    "екб": _city_snapshot(main, start_row=1),
                    "члб": _city_snapshot(main, start_row=5),
                    "тюм": _city_snapshot(main, start_row=9),
                },
            )
        except (IndexError, InvalidOperation, ValueError) as exc:
            log.warning("Unexpected dashboard layout in Google Sheets: %s", exc)
            return None

    async def _read_rates(self) -> list[Decimal | Exception]:
        results: list[Decimal | Exception] = []
        for code in self._sheet_codes:
            try:
                results.append(await self._gateway.read_main_rate(code))
            except Exception as exc:  # noqa: BLE001 - isolate failures from the external SDK
                results.append(exc)
        return results


def _currency_snapshot(
    rows: list[list[str]],
    *,
    start_row: int,
    column: int,
) -> DashboardCurrencySnapshot:
    return DashboardCurrencySnapshot(
        amount=_cell_decimal(rows, start_row, column),
        rate=_cell_decimal(rows, start_row + 1, column),
        rub_value=_cell_decimal(rows, start_row + 2, column),
        client_amount=_cell_decimal(rows, start_row + 3, column),
        fact_amount=_cell_decimal(rows, start_row + 4, column),
    )


def _city_snapshot(rows: list[list[str]], *, start_row: int) -> DashboardCityFinancialSnapshot:
    return DashboardCityFinancialSnapshot(
        income=_cell_decimal(rows, start_row, 14),
        expense=_cell_decimal(rows, start_row + 1, 14),
        profit=_cell_decimal(rows, start_row + 2, 14),
    )


def _expenses_for_date(values: dict[str, list[list[str]]], target: date) -> Decimal:
    total = Decimal(0)
    for range_name in ("Расходы!B3:C1000", "Расходы!H3:I1000"):
        for row in values.get(range_name, []):
            if len(row) < 2 or _parse_sheet_date(row[1]) != target:
                continue
            total += _parse_decimal(row[0])
    return total


def _parse_sheet_date(value: str) -> date | None:
    raw = str(value).strip()
    formats = ("%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y")
    for date_format in formats:
        try:
            return datetime.strptime(raw, date_format).date()
        except ValueError:
            continue
    return None


def _cell_decimal(rows: list[list[str]], row: int, column: int) -> Decimal:
    try:
        return _parse_decimal(rows[row - 1][column - 1])
    except IndexError as exc:
        raise ValueError(f"Required dashboard cell R{row}C{column} is empty") from exc


def _parse_decimal(value: str) -> Decimal:
    normalized = str(value)
    for character in (" ", "\u00a0", "\u202f", "₽", "$", "%"):
        normalized = normalized.replace(character, "")
    normalized = normalized.replace(",", ".").strip()
    if not normalized:
        return Decimal(0)
    return Decimal(normalized)
