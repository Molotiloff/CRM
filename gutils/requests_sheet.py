# gutils/requests_sheet.py
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httplib2
from google.oauth2.service_account import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)


class SheetsWriteError(Exception):
    pass


_SPREADSHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")

# Карта ячеек с внутренними курсами на листе «Главная»
_DEFAULT_CELL_MAP = {
    "EUR": "Главная!E2",
    "USDT": "Главная!E9",
    "USD": "Главная!H9",
    "USDW": "Главная!H2",
    "THB": "Главная!K2",
}

# Кеш только для сервисных объектов (не для значений)
_cached = {
    "service": None,
    "spreadsheet_id": None,
    "creds": None,
}

_GOOGLE_API_TIMEOUT_SECONDS = 30.0
_GOOGLE_API_NUM_RETRIES = 3


def get_service_account_email() -> str:
    try:
        creds = _get_credentials()
        return getattr(creds, "service_account_email", "") or ""
    except Exception:
        log.exception("Could not read service account email")
        return ""


def _extract_id_from_url(maybe_url_or_id: str) -> str:
    s = (maybe_url_or_id or "").strip()
    m = _SPREADSHEET_ID_RE.search(s)
    return m.group(1) if m else s


def _get_credentials() -> Any:
    if _cached["creds"]:
        return _cached["creds"]
    json_inline = os.getenv("GOOGLE_CREDENTIALS_JSON") or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    json_path = (
        os.getenv("GOOGLE_CREDENTIALS_FILE")
        or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if json_inline:
        info = json.loads(json_inline)
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    elif json_path:
        creds = Credentials.from_service_account_file(json_path, scopes=scopes)
    else:
        raise SheetsWriteError("Не заданы креды Google.")
    _cached["creds"] = creds
    return creds


def _get_service():
    if _cached["service"]:
        return _cached["service"]
    authorized_http = AuthorizedHttp(
        _get_credentials(),
        http=httplib2.Http(timeout=_GOOGLE_API_TIMEOUT_SECONDS),
    )
    service = build(
        "sheets",
        "v4",
        http=authorized_http,
        cache_discovery=False,
    )
    _cached["service"] = service
    return service


def _execute(request):
    return request.execute(num_retries=_GOOGLE_API_NUM_RETRIES)


def _resolve_spreadsheet_id(spreadsheet: str | None) -> str:
    if _cached["spreadsheet_id"]:
        return _cached["spreadsheet_id"]
    candidate = (
        spreadsheet
        or os.getenv("GOOGLE_SHEET_URL")
        or os.getenv("GOOGLE_SHEET_ID")
        or os.getenv("SPREADSHEET_URL")
        or os.getenv("SPREADSHEET_ID")
    )
    if not candidate:
        raise SheetsWriteError("Не задан spreadsheet.")
    sid = _extract_id_from_url(candidate)
    _cached["spreadsheet_id"] = sid
    return sid


# === helpers ===
def _coerce_num(x: str | int | float | Decimal) -> str:
    s = format(Decimal(x).normalize(), "f") if isinstance(x, Decimal) else str(x)
    return s.strip().replace(" ", "").replace("\u00a0", "").replace(".", ",")


def _format_dt(value: str | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    raw = str(value).strip()
    if not raw:
        return raw
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except ValueError:
        return raw.split(" ", 1)[0]


def _find_next_row(service, sid: str, sheet: str) -> int:
    resp = _execute(
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=sid,
            range=f"{sheet}!A:A",
        )
    )
    rows = resp.get("values", [])
    return len(rows) + 1 if rows else 2


def _read_main_rate_fresh(code: str, cell_map: dict[str, str] | None = None) -> Decimal | None:
    """Читает актуальный курс из листа «Главная» (без кеша)."""
    if not code:
        return None
    cell_map = cell_map or _DEFAULT_CELL_MAP
    ref = cell_map.get(code.upper())
    if not ref:
        return None
    service = _get_service()
    sid = _resolve_spreadsheet_id(None)
    resp = _execute(
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=sid,
            range=ref,
        )
    )
    vals = resp.get("values", [])
    if not vals or not vals[0]:
        return None
    raw = str(vals[0][0]).replace("\u00a0", "").replace(" ", "").replace(",", ".").strip()
    try:
        val = Decimal(raw)
        return val if val.is_finite() else None
    except InvalidOperation:
        return None


def read_main_rate(code: str, cell_map: dict[str, str] | None = None) -> Decimal:
    value = _read_main_rate_fresh(code, cell_map)
    if value is None:
        raise SheetsWriteError(f"Не найден внутренний курс для {code!r} на листе 'Главная'.")
    return value


def read_sheet_ranges(
    ranges: list[str],
    *,
    spreadsheet: str | None = None,
) -> dict[str, list[list[str]]]:
    """Read several ranges in one Google Sheets API request."""
    if not ranges:
        return {}
    service = _get_service()
    sid = _resolve_spreadsheet_id(spreadsheet)
    request = (
        service.spreadsheets()
        .values()
        .batchGet(
            spreadsheetId=sid,
            ranges=ranges,
            valueRenderOption="FORMATTED_VALUE",
        )
    )
    response = _execute(request)
    value_ranges = response.get("valueRanges", [])
    return {
        requested_range: value_range.get("values", [])
        for requested_range, value_range in zip(ranges, value_ranges, strict=False)
    }


# === Основные функции ===
def append_sale_row(
    *,
    in_currency: str,
    out_currency: str,
    in_amount: str | int | float | Decimal,
    out_amount: str | int | float | Decimal,
    rate: str | int | float | Decimal,
    created_at: str | datetime | None = None,
    spreadsheet: str | None = None,
    sheet_name: str = "Продажа",
    cell_map=None,
    request_id: int | str | None = None,
) -> tuple[int, Decimal | None]:
    """Записывает строку на лист 'Продажа'. Столбец D — свежий курс из «Главная», столбец B — номер заявки."""
    if cell_map is None:
        cell_map = _DEFAULT_CELL_MAP

    try:
        service = _get_service()
        sid = _resolve_spreadsheet_id(spreadsheet)
        row = _find_next_row(service, sid, sheet_name)

        out_cur = (out_currency or "").strip().upper()
        if not out_cur:
            raise SheetsWriteError("Нет валюты (out_currency).")

        val_amount = _coerce_num(out_amount)
        val_rate = _coerce_num(rate)

        # Всегда читаем актуальный курс для D
        val_input = None
        fresh = _read_main_rate_fresh(out_cur, cell_map)
        if fresh is not None:
            val_input = _coerce_num(fresh)

        data = []
        if created_at is not None:
            data.append({"range": f"{sheet_name}!A{row}", "values": [[_format_dt(created_at)]]})
        if request_id is not None:
            data.append({"range": f"{sheet_name}!B{row}", "values": [[str(request_id)]]})
        data.append({"range": f"{sheet_name}!C{row}", "values": [[out_cur]]})
        if val_input is not None:
            data.append({"range": f"{sheet_name}!D{row}", "values": [[val_input]]})
        data.extend(
            [
                {"range": f"{sheet_name}!E{row}", "values": [[val_amount]]},
                {"range": f"{sheet_name}!G{row}", "values": [[val_rate]]},
            ]
        )

        _execute(
            service.spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "USER_ENTERED", "data": data},
            )
        )
        return row, None

    except HttpError as e:
        raise SheetsWriteError(f"Ошибка Google Sheets API: {e}") from e
    except Exception as e:
        raise SheetsWriteError(str(e)) from e


def append_buy_row(
    *,
    currency: str,
    amount: str | int | float | Decimal,
    rate: str | int | float | Decimal,
    created_at: str | datetime | None = None,
    spreadsheet: str | None = None,
    sheet_name: str = "Покупка",
    request_id: int | str | None = None,
) -> int:
    """Записывает строку на лист 'Покупка'. Столбец B — номер заявки."""
    try:
        service = _get_service()
        sid = _resolve_spreadsheet_id(spreadsheet)
        row = _find_next_row(service, sid, sheet_name)

        cur = (currency or "").strip().upper()
        if not cur:
            raise SheetsWriteError("Нет валюты (currency).")

        val_amount = _coerce_num(amount)
        val_rate = _coerce_num(rate)

        data = []
        if created_at is not None:
            data.append({"range": f"{sheet_name}!A{row}", "values": [[_format_dt(created_at)]]})
        if request_id is not None:
            data.append({"range": f"{sheet_name}!B{row}", "values": [[str(request_id)]]})
        data.extend(
            [
                {"range": f"{sheet_name}!C{row}", "values": [[cur]]},
                {"range": f"{sheet_name}!D{row}", "values": [[val_amount]]},
                {"range": f"{sheet_name}!E{row}", "values": [[val_rate]]},
            ]
        )

        _execute(
            service.spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "USER_ENTERED", "data": data},
            )
        )
        return row

    except HttpError as e:
        raise SheetsWriteError(f"Ошибка Google Sheets API: {e}") from e
    except Exception as e:
        raise SheetsWriteError(str(e)) from e


def _find_rows_by_req_id(service, sid: str, sheet_name: str, req_id: int | str) -> list[int]:
    """
    Возвращает список 0-based индексов строк, где в столбце B == req_id.
    Пропускает заголовок (если он есть).
    """
    rng = f"{sheet_name}!B:B"
    resp = _execute(
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=sid,
            range=rng,
        )
    )
    values = resp.get("values", [])
    # values[0] -> B1; 0-based индекс для DeleteDimensionRequest = номер_строки_в_гугле - 1
    hits: list[int] = []
    needle = str(req_id).strip()
    for i, row in enumerate(values):
        cell = (row[0] if row else "").strip()
        if cell == needle:
            # i -> индекс в диапазоне B:B, т.е. это строка (i+1) в листе => 0-based row_index = i
            hits.append(i)
    # если у вас в B1 заголовок и он равен req_id — он тоже попадёт; обычно заголовок иной
    return hits


def _delete_rows_by_0based_indices(
    service, sid: str, sheet_id: int, rows_0based: list[int]
) -> None:
    """
    Удаляет строки по их 0-based индексам через batchUpdate DeleteDimensionRequest.
    Передавайте перечисление индексов В УБЫВАЮЩЕМ порядке.
    """
    if not rows_0based:
        return
    requests = []
    for r in rows_0based:
        requests.append(
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": r,
                        "endIndex": r + 1,
                    }
                }
            }
        )
    _execute(
        service.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": requests},
        )
    )


def _get_sheet_id(service, sid: str, sheet_name: str) -> int:
    meta = _execute(service.spreadsheets().get(spreadsheetId=sid))
    for sh in meta.get("sheets", []):
        props = sh.get("properties", {})
        if props.get("title") == sheet_name:
            return int(props["sheetId"])
    raise SheetsWriteError(f"Не найден лист: {sheet_name}")


def delete_rows_by_request_id(
    *,
    req_id: int | str,
    spreadsheet: str | None = None,
    sheets: tuple[str, str] = ("Покупка", "Продажа"),
) -> dict[str, int]:
    """
    Удаляет ВСЕ строки, где B == req_id, на указанных листах.
    Возвращает словарь: {имя_листа: кол-во_удалённых}.
    """
    try:
        service = _get_service()
        sid = _resolve_spreadsheet_id(spreadsheet)
        results: dict[str, int] = {}
        for sheet_name in sheets:
            sheet_id = _get_sheet_id(service, sid, sheet_name)
            rows = _find_rows_by_req_id(service, sid, sheet_name, req_id)
            if rows:
                # удаляем снизу вверх
                rows_sorted = sorted(rows, reverse=True)
                _delete_rows_by_0based_indices(service, sid, sheet_id, rows_sorted)
            results[sheet_name] = len(rows or [])
        return results
    except HttpError as e:
        raise SheetsWriteError(f"Ошибка Google Sheets API: {e}") from e
    except Exception as e:
        raise SheetsWriteError(str(e)) from e
