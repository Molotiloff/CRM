from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol


class SyncSheetsTradeGateway(Protocol):
    def get_service_account_email(self) -> str: ...

    def read_main_rate(self, code: str, cell_map: dict[str, str] | None = None) -> Decimal: ...

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
    ) -> tuple[int, Decimal | None]: ...

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
    ) -> int: ...

    def delete_rows_by_request_id(
        self,
        *,
        req_id: int | str,
        spreadsheet: str | None = None,
        sheets: tuple[str, str] = ("Покупка", "Продажа"),
    ) -> dict[str, int]: ...

    def read_sheet_ranges(self, ranges: list[str]) -> dict[str, list[list[str]]]: ...


class AsyncSheetsTradeGateway(Protocol):
    async def get_service_account_email(self) -> str: ...

    async def read_main_rate(
        self,
        code: str,
        cell_map: dict[str, str] | None = None,
    ) -> Decimal: ...

    async def read_sheet_ranges(
        self,
        ranges: list[str],
    ) -> dict[str, list[list[str]]]: ...

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
    ) -> tuple[int, Decimal | None]: ...

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
    ) -> int: ...

    async def delete_rows_by_request_id(
        self,
        *,
        req_id: int | str,
        spreadsheet: str | None = None,
        sheets: tuple[str, str] = ("Покупка", "Продажа"),
    ) -> dict[str, int]: ...
