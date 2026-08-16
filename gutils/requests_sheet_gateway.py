from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from gutils.requests_sheet import (
    append_buy_row,
    append_sale_row,
    delete_rows_by_request_id,
    get_service_account_email,
    read_main_rate,
    read_sheet_ranges,
)
from services.request_table.sheets_trade_gateway import SyncSheetsTradeGateway
from services.thread_adapter import SerializedThreadExecutor


class GutilsSheetsTradeGateway:
    def get_service_account_email(self) -> str:
        return get_service_account_email()

    def read_main_rate(self, code: str, cell_map: dict[str, str] | None = None) -> Decimal:
        return read_main_rate(code, cell_map)

    def read_sheet_ranges(self, ranges: list[str]) -> dict[str, list[list[str]]]:
        return read_sheet_ranges(ranges)

    def append_sale_row(
        self,
        *,
        in_currency: str,
        out_currency: str,
        in_amount: Decimal,
        out_amount: Decimal,
        rate: Decimal,
        created_at: datetime | None = None,
        spreadsheet: str | None = None,
        sheet_name: str = "Продажа",
        cell_map: dict[str, str] | None = None,
        request_id: int | str | None = None,
    ) -> tuple[int, Decimal | None]:
        return append_sale_row(
            in_currency=in_currency,
            out_currency=out_currency,
            in_amount=in_amount,
            out_amount=out_amount,
            rate=rate,
            created_at=created_at,
            spreadsheet=spreadsheet,
            sheet_name=sheet_name,
            cell_map=cell_map,
            request_id=request_id,
        )

    def append_buy_row(
        self,
        *,
        currency: str,
        amount: Decimal,
        rate: Decimal,
        created_at: datetime | None = None,
        spreadsheet: str | None = None,
        sheet_name: str = "Покупка",
        request_id: int | str | None = None,
    ) -> int:
        return append_buy_row(
            currency=currency,
            amount=amount,
            rate=rate,
            created_at=created_at,
            spreadsheet=spreadsheet,
            sheet_name=sheet_name,
            request_id=request_id,
        )

    def delete_rows_by_request_id(
        self,
        *,
        req_id: int | str,
        spreadsheet: str | None = None,
        sheets: tuple[str, str] = ("Покупка", "Продажа"),
    ) -> dict[str, int]:
        return delete_rows_by_request_id(
            req_id=req_id,
            spreadsheet=spreadsheet,
            sheets=sheets,
        )


class ThreadedSheetsTradeGateway:
    """Serializes the synchronous Google SDK behind an async application port."""

    def __init__(self, gateway: SyncSheetsTradeGateway) -> None:
        self._gateway = gateway
        self._executor = SerializedThreadExecutor(task_name="google_sheets_io")

    async def get_service_account_email(self) -> str:
        return await self._executor.run(self._gateway.get_service_account_email)

    async def read_main_rate(
        self,
        code: str,
        cell_map: dict[str, str] | None = None,
    ) -> Decimal:
        return await self._executor.run(self._gateway.read_main_rate, code, cell_map)

    async def read_sheet_ranges(
        self,
        ranges: list[str],
    ) -> dict[str, list[list[str]]]:
        return await self._executor.run(self._gateway.read_sheet_ranges, ranges)

    async def append_sale_row(
        self,
        *,
        in_currency: str,
        out_currency: str,
        in_amount: Decimal,
        out_amount: Decimal,
        rate: Decimal,
        created_at: datetime | None = None,
        spreadsheet: str | None = None,
        sheet_name: str = "Продажа",
        cell_map: dict[str, str] | None = None,
        request_id: int | str | None = None,
    ) -> tuple[int, Decimal | None]:
        return await self._executor.run(
            self._gateway.append_sale_row,
            in_currency=in_currency,
            out_currency=out_currency,
            in_amount=in_amount,
            out_amount=out_amount,
            rate=rate,
            created_at=created_at,
            spreadsheet=spreadsheet,
            sheet_name=sheet_name,
            cell_map=cell_map,
            request_id=request_id,
        )

    async def append_buy_row(
        self,
        *,
        currency: str,
        amount: Decimal,
        rate: Decimal,
        created_at: datetime | None = None,
        spreadsheet: str | None = None,
        sheet_name: str = "Покупка",
        request_id: int | str | None = None,
    ) -> int:
        return await self._executor.run(
            self._gateway.append_buy_row,
            currency=currency,
            amount=amount,
            rate=rate,
            created_at=created_at,
            spreadsheet=spreadsheet,
            sheet_name=sheet_name,
            request_id=request_id,
        )

    async def delete_rows_by_request_id(
        self,
        *,
        req_id: int | str,
        spreadsheet: str | None = None,
        sheets: tuple[str, str] = ("Покупка", "Продажа"),
    ) -> dict[str, int]:
        return await self._executor.run(
            self._gateway.delete_rows_by_request_id,
            req_id=req_id,
            spreadsheet=spreadsheet,
            sheets=sheets,
        )
