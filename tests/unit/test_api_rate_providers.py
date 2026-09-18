from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import cast

import pytest

from api.rate_providers import DashboardSheetsGateway, GutilsFirmRateProvider


class FakeSheetsGateway:
    async def read_main_rate(self, code: str, cell_map=None) -> Decimal:
        if code == "EUR":
            raise RuntimeError("sheet cell is unavailable")
        return {
            "USDT": Decimal("81.25"),
            "USD": Decimal("88.5"),
            "USDW": Decimal("90.1"),
        }[code]


class TrackingSheetsGateway:
    def __init__(self) -> None:
        self.active_calls = 0
        self.max_active_calls = 0

    async def read_main_rate(self, code: str, cell_map=None) -> Decimal:
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            await asyncio.sleep(0.005)
            return Decimal("1")
        finally:
            self.active_calls -= 1


class FakeDashboardSheetsGateway:
    async def read_main_rate(self, code: str, cell_map=None) -> Decimal:
        return Decimal("1")

    async def read_sheet_ranges(self, ranges: list[str]) -> dict[str, list[list[str]]]:
        main = [[""] * 14 for _ in range(14)]

        def set_cell(row: int, column: int, value: str) -> None:
            main[row - 1][column - 1] = value

        for row, value in {
            8: "2 029 905₽",
            9: "228 598₽",
            10: "1 801 307₽",
            11: "-23₽",
            13: "250 000₽",
            14: "36 833 394₽",
        }.items():
            set_cell(row, 2, value)
        set_cell(13, 5, "50 000 000₽")
        set_cell(14, 5, "4%")
        for row, value in {
            1: "43 528 093₽",
            3: "4 404 248₽",
            4: "-39 123 845₽",
            5: "-34 719 596₽",
            7: "-38 694 123₽",
            8: "-429 722₽",
            10: "2 081 592₽",
            11: "2 322 656₽",
            13: "35 032 087₽",
        }.items():
            set_cell(row, 11, value)
        for column, start_row, values in (
            (5, 1, ("10056", "95,704", "962397", "-56", "10000")),
            (5, 7, ("2597", "85,28", "221467", "2409", "5579")),
            (8, 1, ("0", "0", "0", "0", "0")),
            (8, 7, ("13500", "84,355", "1138793", "0", "13500")),
        ):
            for offset, value in enumerate(values):
                set_cell(start_row + offset, column, value)
        for start_row, values in (
            (1, ("839883", "27365", "812518")),
            (5, ("420687", "29500", "391187")),
            (9, ("225200", "66500", "158700")),
        ):
            for offset, value in enumerate(values):
                set_cell(start_row + offset, 14, value)
        return {
            "Главная!A1:N14": main,
            "Расходы!B3:C1000": [["1000₽", "11.08.2026"]],
            "Расходы!H3:I1000": [["2 500₽", "08/11/2026"]],
        }


@pytest.mark.asyncio
async def test_gutils_rate_provider_normalizes_codes_and_keeps_partial_result() -> None:
    provider = GutilsFirmRateProvider(cast(DashboardSheetsGateway, FakeSheetsGateway()))

    rates = await provider.get_rates()

    assert rates == {
        "RUB": Decimal("1"),
        "USDT": Decimal("81.25"),
        "USD_BL": Decimal("88.5"),
        "USD_WH": Decimal("90.1"),
    }


@pytest.mark.asyncio
async def test_gutils_rate_provider_serializes_gateway_access() -> None:
    gateway = TrackingSheetsGateway()
    provider = GutilsFirmRateProvider(cast(DashboardSheetsGateway, gateway))

    await asyncio.gather(provider.get_rates(), provider.get_rates())

    assert gateway.max_active_calls == 1


@pytest.mark.asyncio
async def test_gutils_rate_provider_maps_dashboard_snapshot_in_one_read() -> None:
    gateway = FakeDashboardSheetsGateway()
    provider = GutilsFirmRateProvider(cast(DashboardSheetsGateway, gateway))

    snapshot = await provider.get_snapshot(today=date(2026, 8, 11))

    assert snapshot is not None
    assert snapshot.profit == Decimal("1801307")
    assert snapshot.today_expense == Decimal("3500")
    assert snapshot.daily_profit == Decimal("250000")
    assert snapshot.turnover == Decimal("36833394")
    assert snapshot.monthly_turnover == Decimal("50000000")
    assert snapshot.profitability == Decimal("0.04")
    assert snapshot.invested_capital == Decimal("35032087")
    assert snapshot.currencies["USDT"].rate == Decimal("85.28")
    assert snapshot.cities["екб"].profit == Decimal("812518")
