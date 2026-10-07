from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from gutils.requests_sheet import MAIN_RATE_CELL_MAP, SheetsWriteError
from services.request_table.sheets_trade_gateway import AsyncSheetsTradeGateway

if TYPE_CHECKING:
    from db_asyncpg.ports.exchange import ExchangeRequestRepositoryPort
    from services.crm.deal_service import DealService


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
    _global_write_lock = asyncio.Lock()
    _RUB_CODES = {"RUB", "РУБМСК", "РУБСПБ", "РУБПЕР", "РУБТЮМ"}
    _FIAT_CODES = frozenset({"EUR", "EUR500", "USD", "USDW", "THB"})

    _TABLE_CURRENCY_NAMES = {
        "USD": "USD BL",
        "USDW": "USD WH",
        "EUR": "EUR",
        "EUR500": "EUR",
        "USDT": "USDT",
        "THB": "THB",
    }

    _SEP = {" ", "\u00a0", "\u202f", "\u2009", "'", "’", "ʼ", "‛", "`"}

    def __init__(self, *, sheets_gateway: AsyncSheetsTradeGateway) -> None:
        self.sheets_gateway = sheets_gateway
        self._write_lock = self._global_write_lock

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

    async def write_exchange_request_once(
        self,
        *,
        table_req_id: str,
        repo: ExchangeRequestRepositoryPort,
        deal_service: DealService | None,
        message_dt: datetime | None,
        actor_user_id: int | None = None,
    ) -> TableDoneResult | None:
        async with self._write_lock:
            async with repo.table_write_lock(table_req_id):
                row = await repo.get_exchange_request_link_by_table_req_id(
                    table_req_id=table_req_id
                )
                if row is None:
                    raise SheetsWriteError("Заявка для записи в таблицу не найдена")
                if row.get("status") == "cancelled":
                    raise SheetsWriteError("Отменённую заявку нельзя занести в таблицу")
                if row.get("is_table_done"):
                    if deal_service is not None:
                        await deal_service.complete_exchange_after_table(
                            table_req_id, actor_user_id=actor_user_id
                        )
                    return None
                payload = self.payload_from_db_row(row)
                if payload is None:
                    raise SheetsWriteError("Параметры заявки для таблицы повреждены")
                result = await self._write_by_payload(payload=payload, message_dt=message_dt)
                marked = await repo.mark_exchange_request_table_done(
                    table_req_id=table_req_id, is_table_done=True
                )
                if not marked:
                    raise SheetsWriteError("Запись создана, но отметка в БД не сохранена: нужна сверка")
                if deal_service is not None:
                    try:
                        await deal_service.complete_exchange_after_table(
                            table_req_id, actor_user_id=actor_user_id
                        )
                    except Exception as exc:
                        raise SheetsWriteError(
                            "Запись в таблице выполнена, но статус CRM не обновился. "
                            "Повторите кнопку: строка повторно не добавится"
                        ) from exc
                return result

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

        if in_cur == "USDT" and out_cur not in self._FIAT_CODES:
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

        elif out_cur == "USDT" and in_cur not in self._FIAT_CODES:
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

        elif out_cur in self._FIAT_CODES and in_cur_table == "RUB":
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

        elif in_cur in self._FIAT_CODES and out_cur_table == "RUB":
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

        elif in_cur in self._FIAT_CODES and out_cur == "USDT":
            inner_rate = await self.sheets_gateway.read_main_rate(
                in_cur,
                MAIN_RATE_CELL_MAP,
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

        elif in_cur == "USDT" and out_cur in self._FIAT_CODES:
            inner_rate = await self.sheets_gateway.read_main_rate(
                out_cur,
                MAIN_RATE_CELL_MAP,
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

        elif in_cur in self._FIAT_CODES and out_cur in self._FIAT_CODES:
            in_rate = await self.sheets_gateway.read_main_rate(
                in_cur,
                MAIN_RATE_CELL_MAP,
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
            custom_cell_map = dict(MAIN_RATE_CELL_MAP)
            if pretty_out not in custom_cell_map:
                custom_cell_map[pretty_out] = MAIN_RATE_CELL_MAP[out_cur]
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
