from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from api.schemas.balances import BalanceClientDto, BalancesSnapshotResponse, CurrencySummaryDto

from .common import client_number, decimal_value, float_value, initials, tone


def build_balances_snapshot(
    *,
    rows: list[dict[str, Any]],
    rub_rates: Mapping[str, Decimal],
) -> BalancesSnapshotResponse:
    clients = [
        BalanceClientDto(
            id=str(row["client_id"]),
            name=str(row["client_name"] or ""),
            clientNumber=client_number(int(row["client_id"])),
            currency=str(row["currency_code"]).upper(),
            balance=float_value(row["balance"]),
            balanceRub=float_value(
                decimal_value(row["balance"])
                * rub_rates.get(str(row["currency_code"]).upper(), Decimal(0))
            ),
            initials=initials(str(row["client_name"] or "")),
            telegramChatId=str(row["chat_id"]) if row.get("chat_id") is not None else None,
        )
        for row in rows
    ]
    by_currency: dict[str, Decimal] = {}
    total_rub = Decimal(0)
    for row in rows:
        code = str(row["currency_code"]).upper()
        amount = decimal_value(row["balance"])
        by_currency[code] = by_currency.get(code, Decimal(0)) + amount
        total_rub += amount * rub_rates.get(code, Decimal(0))

    summaries = [
        CurrencySummaryDto(
            code=code,
            label=f"{code} клиент.",
            value=float_value(amount),
            tone=tone(amount),
        )
        for code, amount in sorted(by_currency.items())
    ]
    summaries.append(
        CurrencySummaryDto(
            code="ALL",
            label="Все балансы",
            value=float_value(total_rub),
            tone=tone(total_rub),
        )
    )
    return BalancesSnapshotResponse(clients=clients, summaries=summaries)
