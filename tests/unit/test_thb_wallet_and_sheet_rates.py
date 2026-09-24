from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gutils import requests_sheet
from gutils.requests_sheet import MAIN_RATE_CELL_MAP
from services.admin_client.client_bootstrap_service import ClientBootstrapService
from services.request_table.table_done_service import RequestTableDoneService, TableDonePayload


async def test_start_adds_thb_to_existing_client_wallet() -> None:
    repo = SimpleNamespace(
        ensure_client=AsyncMock(return_value=42),
        snapshot_wallet=AsyncMock(
            return_value=[
                {"currency_code": code}
                for code in ("USD", "USDW", "USDT", "RUB", "EUR", "EUR500", "РУБПЕР")
            ]
        ),
        add_currency=AsyncMock(),
    )

    client_id = await ClientBootstrapService(repo).ensure_client_wallet(
        chat_id=-100, chat_name="Клиент"
    )

    assert client_id == 42
    repo.add_currency.assert_awaited_once_with(42, "THB", 2)


class TradeGatewayStub:
    def __init__(self) -> None:
        self.read_calls: list[tuple[str, str]] = []
        self.buy_rows: list[dict] = []
        self.sale_rows: list[dict] = []
        self.write_order: list[str] = []

    async def read_main_rate(self, code: str, cell_map: dict[str, str]) -> Decimal:
        self.read_calls.append((code, cell_map[code]))
        return Decimal("4") if code == "THB" else Decimal("90")

    async def append_buy_row(self, **kwargs) -> None:
        self.buy_rows.append(kwargs)
        self.write_order.append("Покупка")

    async def append_sale_row(self, **kwargs) -> None:
        self.sale_rows.append(kwargs)
        self.write_order.append("Продажа")


@pytest.mark.parametrize(
    ("in_cur", "out_cur", "rate_code", "rate_cell", "buy_currency", "sale_currency"),
    [
        ("EUR", "USDT", "EUR", "Главная!E2", "EUR", "USDT"),
        ("USD", "USDT", "USD", "Главная!H9", "USD BL", "USDT"),
        ("USDW", "USDT", "USDW", "Главная!H2", "USD WH", "USDT"),
        ("THB", "USDT", "THB", "Главная!K2", "THB", "USDT"),
        ("USDT", "THB", "THB", "Главная!K2", "USDT", "THB"),
        ("USDT", "USD", "USD", "Главная!H9", "USDT", "USD BL"),
    ],
)
async def test_cross_currency_trade_uses_current_main_rate_cells(
    in_cur: str,
    out_cur: str,
    rate_code: str,
    rate_cell: str,
    buy_currency: str,
    sale_currency: str,
) -> None:
    gateway = TradeGatewayStub()
    service = RequestTableDoneService(sheets_gateway=gateway)

    await service.write_by_payload(
        payload=TableDonePayload(
            req_id=123,
            in_cur=in_cur,
            out_cur=out_cur,
            in_amt=Decimal("100"),
            out_amt=Decimal("10"),
            rate=Decimal("10"),
        ),
        message_dt=None,
    )

    assert gateway.read_calls == [(rate_code, rate_cell)]
    assert [row["currency"] for row in gateway.buy_rows] == [buy_currency]
    assert [row["out_currency"] for row in gateway.sale_rows] == [sale_currency]
    assert gateway.buy_rows[0]["request_id"] == gateway.sale_rows[0]["request_id"] == 123
    assert gateway.write_order == (
        ["Покупка", "Продажа"] if out_cur == "USDT" else ["Продажа", "Покупка"]
    )


def test_main_rate_cell_map_matches_current_sheet_layout() -> None:
    assert MAIN_RATE_CELL_MAP == {
        "EUR": "Главная!E2",
        "USDT": "Главная!E9",
        "USD": "Главная!H9",
        "USDW": "Главная!H2",
        "THB": "Главная!K2",
    }


@pytest.mark.parametrize(
    ("currency", "expected_cell"),
    [
        ("USD BL", "Главная!H9"),
        ("USD WH", "Главная!H2"),
        ("USDT", "Главная!E9"),
        ("EUR", "Главная!E2"),
        ("THB", "Главная!K2"),
    ],
)
def test_sale_row_fills_input_rate_for_sheet_currency_names(
    monkeypatch: pytest.MonkeyPatch,
    currency: str,
    expected_cell: str,
) -> None:
    service = MagicMock()
    values = service.spreadsheets.return_value.values.return_value
    values.get.return_value.execute.return_value = {"values": [["87,072"]]}
    monkeypatch.setattr(requests_sheet, "_get_service", lambda: service)
    monkeypatch.setattr(requests_sheet, "_resolve_spreadsheet_id", lambda _: "sheet-id")
    monkeypatch.setattr(requests_sheet, "_find_next_row", lambda *_: 312)

    requests_sheet.append_sale_row(
        in_currency="RUB",
        out_currency=currency,
        in_amount=Decimal("10000"),
        out_amount=Decimal("114"),
        rate=Decimal("88"),
    )

    values.get.assert_called_once_with(spreadsheetId="sheet-id", range=expected_cell)
    data = values.batchUpdate.call_args.kwargs["body"]["data"]
    assert {item["range"]: item["values"] for item in data}["Продажа!D312"] == [["87,072"]]


def test_sale_row_does_not_write_when_known_input_rate_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MagicMock()
    values = service.spreadsheets.return_value.values.return_value
    values.get.return_value.execute.return_value = {"values": []}
    monkeypatch.setattr(requests_sheet, "_get_service", lambda: service)
    monkeypatch.setattr(requests_sheet, "_resolve_spreadsheet_id", lambda _: "sheet-id")
    monkeypatch.setattr(requests_sheet, "_find_next_row", lambda *_: 312)

    with pytest.raises(requests_sheet.SheetsWriteError, match="USD BL"):
        requests_sheet.append_sale_row(
            in_currency="RUB",
            out_currency="USD BL",
            in_amount=Decimal("10000"),
            out_amount=Decimal("114"),
            rate=Decimal("88"),
        )

    values.batchUpdate.assert_not_called()
