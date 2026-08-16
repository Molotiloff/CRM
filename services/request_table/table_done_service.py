from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from gutils.requests_sheet import SheetsWriteError
from services.request_table.sheets_trade_gateway import AsyncSheetsTradeGateway


@dataclass(slots=True, frozen=True)
class TableDonePayload:
    req_id: int | None
    in_cur: str
    out_cur: str
    in_amt: Decimal
    out_amt: Decimal
    rate: Decimal


@dataclass(slots=True, frozen=True)
class TableDoneResult:
    sheet_type: str
    in_cur: str
    out_cur: str
    in_amt: Decimal
    out_amt: Decimal
    rate: Decimal


class RequestTableDoneService:
    _RUB_CODES = {"RUB", "РУБМСК", "РУБСПБ", "РУБПЕР", "РУБТЮМ"}

    _DEFAULT_CELL_MAP = {
        "EUR": "Главная!E2",
        "USDT": "Главная!E8",
        "USD": "Главная!H8",
        "USDW": "Главная!H2",
    }

    _TABLE_CURRENCY_NAMES = {
        "USD": "USD BL",
        "USDW": "USD WH",
        "EUR": "EUR",
        "USDT": "USDT",
    }

    _SEP = {" ", "\u00a0", "\u202f", "\u2009", "'", "’", "ʼ", "‛", "`"}

    def __init__(self, *, sheets_gateway: AsyncSheetsTradeGateway) -> None:
        self.sheets_gateway = sheets_gateway
        self._write_lock = asyncio.Lock()

    @classmethod
    def _to_decimal(cls, raw: str) -> Decimal:
        value = (raw or "").strip().replace(",", ".")
        for ch in cls._SEP:
            value = value.replace(ch, "")
        return Decimal(value)

    @classmethod
    def parse_callback_payload(cls, data: str) -> TableDonePayload | None:
        parts = (data or "").split(":")
        try:
            if len(parts) >= 8 and parts[0] == "req" and parts[1] == "table_done":
                return TableDonePayload(
                    req_id=int(parts[2]),
                    in_cur=parts[3].strip().upper(),
                    out_cur=parts[4].strip().upper(),
                    in_amt=cls._to_decimal(parts[5]),
                    out_amt=cls._to_decimal(parts[6]),
                    rate=cls._to_decimal(parts[7]),
                )
            if len(parts) >= 7 and parts[0] == "req" and parts[1] == "table_done":
                return TableDonePayload(
                    req_id=None,
                    in_cur=parts[2].strip().upper(),
                    out_cur=parts[3].strip().upper(),
                    in_amt=cls._to_decimal(parts[4]),
                    out_amt=cls._to_decimal(parts[5]),
                    rate=cls._to_decimal(parts[6]),
                )
        except (InvalidOperation, ValueError, IndexError):
            return None
        return None

    @classmethod
    def parse_table_req_id(cls, data: str) -> str | None:
        parts = (data or "").split(":")
        if len(parts) == 3 and parts[0] == "req" and parts[1] == "table_done" and parts[2].strip():
            return parts[2].strip()
        return None

    @classmethod
    def payload_from_db_row(cls, row: dict) -> TableDonePayload | None:
        try:
            req_id = int(str(row["table_req_id"]))
            return TableDonePayload(
                req_id=req_id,
                in_cur=str(row["table_in_cur"]).strip().upper(),
                out_cur=str(row["table_out_cur"]).strip().upper(),
                in_amt=cls._to_decimal(str(row["table_in_amount"])),
                out_amt=cls._to_decimal(str(row["table_out_amount"])),
                rate=cls._to_decimal(str(row["table_rate"])),
            )
        except (KeyError, TypeError, InvalidOperation, ValueError):
            return None

    @classmethod
    def _map_table_currency(cls, cur: str) -> str:
        if (cur or "").strip().upper() in cls._RUB_CODES:
            return "RUB"
        return cls._TABLE_CURRENCY_NAMES.get(cur, cur)

    @staticmethod
    def _message_time(message_dt: datetime | None) -> datetime | None:
        if not isinstance(message_dt, datetime):
            return None
        if message_dt.tzinfo is None:
            message_dt = message_dt.replace(tzinfo=UTC)
        return message_dt.astimezone(timezone(timedelta(hours=5)))

    async def write_by_payload(
        self,
        *,
        payload: TableDonePayload,
        message_dt: datetime | None,
    ) -> TableDoneResult:
        async with self._write_lock:
            return await self._write_by_payload(
                payload=payload,
                message_dt=message_dt,
            )

    async def _write_by_payload(
        self,
        *,
        payload: TableDonePayload,
        message_dt: datetime | None,
    ) -> TableDoneResult:
        created_at = self._message_time(message_dt)
        req_id = payload.req_id
        in_cur = payload.in_cur
        out_cur = payload.out_cur
        in_amt = payload.in_amt
        out_amt = payload.out_amt
        rate = payload.rate
        in_cur_table = self._map_table_currency(in_cur)
        out_cur_table = self._map_table_currency(out_cur)

        if in_cur == "USDT" and out_cur not in {"EUR", "USD", "USDW"}:
            await self.sheets_gateway.append_buy_row(
                currency="USDT",
                amount=in_amt,
                rate=rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Покупка",
                request_id=req_id,
            )
            sheet_type = "Покупка"

        elif out_cur == "USDT" and in_cur not in {"EUR", "USD", "USDW"}:
            await self.sheets_gateway.append_sale_row(
                in_currency=in_cur_table,
                out_currency="USDT",
                in_amount=in_amt,
                out_amount=out_amt,
                rate=rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Продажа",
                request_id=req_id,
            )
            sheet_type = "Продажа"

        elif out_cur in {"EUR", "USD", "USDW"} and in_cur_table == "RUB":
            await self.sheets_gateway.append_sale_row(
                in_currency="RUB",
                out_currency=out_cur_table,
                in_amount=in_amt,
                out_amount=out_amt,
                rate=rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Продажа",
                request_id=req_id,
            )
            sheet_type = "Продажа"

        elif in_cur in {"EUR", "USD", "USDW"} and out_cur_table == "RUB":
            await self.sheets_gateway.append_buy_row(
                currency=in_cur_table,
                amount=in_amt,
                rate=rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Покупка",
                request_id=req_id,
            )
            sheet_type = "Покупка"

        elif in_cur in {"EUR", "USD", "USDW"} and out_cur == "USDT":
            inner_rate = await self.sheets_gateway.read_main_rate(
                in_cur,
                self._DEFAULT_CELL_MAP,
            )
            rub_total = in_amt * inner_rate
            await self.sheets_gateway.append_buy_row(
                currency=in_cur_table,
                amount=in_amt,
                rate=inner_rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Покупка",
                request_id=req_id,
            )
            final_rate = rub_total / out_amt
            await self.sheets_gateway.append_sale_row(
                in_currency=in_cur_table,
                out_currency="USDT",
                in_amount=in_amt,
                out_amount=out_amt,
                rate=final_rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Продажа",
                request_id=req_id,
            )
            sheet_type = f"Покупка + Продажа ({in_cur_table})"

        elif in_cur == "USDT" and out_cur in {"EUR", "USD", "USDW"}:
            inner_rate = await self.sheets_gateway.read_main_rate(
                out_cur,
                self._DEFAULT_CELL_MAP,
            )
            rub_total = out_amt * inner_rate
            await self.sheets_gateway.append_sale_row(
                in_currency="USDT",
                out_currency=out_cur_table,
                in_amount=in_amt,
                out_amount=out_amt,
                rate=inner_rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Продажа",
                request_id=req_id,
            )
            final_rate = rub_total / in_amt
            await self.sheets_gateway.append_buy_row(
                currency="USDT",
                amount=in_amt,
                rate=final_rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Покупка",
                request_id=req_id,
            )
            sheet_type = f"Продажа + Покупка ({out_cur_table})"

        elif in_cur in {"EUR", "USD", "USDW"} and out_cur in {"EUR", "USD", "USDW"}:
            in_rate = await self.sheets_gateway.read_main_rate(
                in_cur,
                self._DEFAULT_CELL_MAP,
            )
            rub_total = in_amt * in_rate
            await self.sheets_gateway.append_buy_row(
                currency=in_cur_table,
                amount=in_amt,
                rate=in_rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Покупка",
                request_id=req_id,
            )
            if out_amt <= 0:
                raise SheetsWriteError("Сумма продажи должна быть > 0.")
            sale_rate = rub_total / out_amt
            pretty_out = out_cur_table
            custom_cell_map = dict(self._DEFAULT_CELL_MAP)
            if pretty_out not in custom_cell_map:
                custom_cell_map[pretty_out] = self._DEFAULT_CELL_MAP[out_cur]
            await self.sheets_gateway.append_sale_row(
                in_currency=in_cur,
                out_currency=pretty_out,
                in_amount=in_amt,
                out_amount=out_amt,
                rate=sale_rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Продажа",
                cell_map=custom_cell_map,
                request_id=req_id,
            )
            sheet_type = f"Покупка + Продажа ({in_cur_table}→{out_cur_table})"

        else:
            raise SheetsWriteError("Неизвестная пара валют. Запись не выполнена.")

        return TableDoneResult(
            sheet_type=sheet_type,
            in_cur=in_cur,
            out_cur=out_cur,
            in_amt=in_amt,
            out_amt=out_amt,
            rate=rate,
        )
