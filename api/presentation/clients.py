from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from api.schemas.clients import (
    ClientBalanceDto,
    ClientCommentDto,
    ClientDto,
    ClientMetricDto,
    ClientRecentDealDto,
    ClientsPageResponse,
    ClientTransactionDto,
)

from .common import client_number, decimal_value, float_value, initials


def build_clients_page(
    *,
    clients: list[dict[str, Any]],
    total_clients: int,
    balances: Mapping[int, list[dict[str, Any]]],
    stats: Mapping[int, dict[str, Any]],
    recent_transactions: Mapping[int, list[dict[str, Any]]],
) -> ClientsPageResponse:
    client_dtos = [
        _client_dto(
            client,
            balances=balances.get(int(client["id"]), []),
            stats=stats.get(int(client["id"]), {}),
            recent_transactions=recent_transactions.get(int(client["id"]), []),
        )
        for client in clients
    ]
    active_with_balance = sum(
        1 for rows in balances.values() if any(decimal_value(row["balance"]) for row in rows)
    )
    total_turnover = sum(decimal_value(row.turnoverRub) for row in client_dtos)
    total_transactions = sum(row.dealsCount for row in client_dtos)
    groups = {
        str(client.get("client_group") or "") for client in clients if client.get("client_group")
    }
    return ClientsPageResponse(
        metrics=[
            _metric(
                "total", "Клиентов в базе", total_clients, "активные клиенты", "purple", "clients"
            ),
            _metric(
                "active",
                "Клиентов с балансом",
                active_with_balance,
                "ненулевые счета",
                "green",
                "check-circle",
            ),
            _metric("groups", "Групп клиентов", len(groups), "client_group", "blue", "plus"),
            _metric(
                "transactions", "Операций", total_transactions, "transactions", "orange", "deals"
            ),
            _metric(
                "turnover",
                "Оборот RUB",
                round(float(total_turnover)),
                "по рублёвым операциям",
                "purple",
                "coins",
            ),
        ],
        clients=client_dtos,
        totalClients=total_clients,
    )


def build_client(
    *,
    client: dict[str, Any],
    balances: list[dict[str, Any]],
    stats: dict[str, Any],
    recent_transactions: list[dict[str, Any]],
) -> ClientDto:
    return _client_dto(
        client,
        balances=balances,
        stats=stats,
        recent_transactions=recent_transactions,
    )


def build_transactions(rows: list[dict[str, Any]], *, limit: int) -> list[ClientTransactionDto]:
    return [
        ClientTransactionDto(
            id=str(row["id"]),
            txnAt=_iso(row["txn_at"]),
            currency=str(row["currency_code"]).upper(),
            amount=float_value(row["amount"]),
            balanceAfter=float_value(row["balance_after"]),
            comment=row.get("comment"),
            source=row.get("source"),
            groupName=row.get("group_name"),
            actorName=row.get("actor_name"),
        )
        for row in rows[:limit]
    ]


def _client_dto(
    client: dict[str, Any],
    *,
    balances: list[dict[str, Any]],
    stats: dict[str, Any],
    recent_transactions: list[dict[str, Any]],
) -> ClientDto:
    client_id = int(client["id"])
    turnover_rub = decimal_value(stats.get("turnover_rub"))
    deals_count = int(stats.get("deals_count") or 0)
    purchase_volume = decimal_value(stats.get("purchase_volume_rub"))
    sale_volume = decimal_value(stats.get("sale_volume_rub"))
    return ClientDto(
        id=str(client_id),
        clientNumber=client_number(client_id),
        name=str(client["name"] or ""),
        initials=initials(str(client["name"] or "")),
        telegramUsername="",
        telegramChatId=str(client["chat_id"]),
        dealsCount=deals_count,
        turnoverRub=float_value(turnover_rub),
        managerName="—",
        registrationDate=_date(client.get("created_at")),
        comment=str(client["client_group"]) if client.get("client_group") else None,
        balances=[
            ClientBalanceDto(
                currency=str(row["currency_code"]).upper(),
                amount=float_value(row["balance"]),
            )
            for row in balances
        ],
        totalProfitRub=0,
        purchaseVolumeRub=float_value(purchase_volume),
        saleVolumeRub=float_value(sale_volume),
        averageCheckRub=float_value(turnover_rub / deals_count if deals_count else Decimal(0)),
        recentDeals=[_recent_deal(row) for row in recent_transactions],
        comments=[
            ClientCommentDto(
                id=f"client-group-{client_id}",
                date=_date(client.get("created_at")),
                author="CRM",
                text=f"Группа клиента: {client['client_group']}",
            )
        ]
        if client.get("client_group")
        else [],
    )


def _recent_deal(row: dict[str, Any]) -> ClientRecentDealDto:
    code = str(row["currency_code"]).upper()
    amount = decimal_value(row["amount"])
    return ClientRecentDealDto(
        id=f"#{row['id']}",
        type="Пополнение" if amount > 0 else "Списание",
        direction=code,
        amountRub=float_value(abs(amount) if code == "RUB" else Decimal(0)),
        time=_relative_time(row.get("txn_at")),
    )


def _metric(
    metric_id: str,
    title: str,
    value: int,
    subtitle: str,
    tone: str,
    icon: str,
) -> ClientMetricDto:
    return ClientMetricDto(
        id=metric_id,
        title=title,
        value=value,
        changePercent=0,
        subtitle=subtitle,
        tone=tone,
        icon=icon,
    )


def _date(value: Any) -> str:
    return value.strftime("%d.%m.%Y") if isinstance(value, datetime) else ""


def _iso(value: Any) -> str:
    return value.astimezone(UTC).isoformat() if isinstance(value, datetime) else ""


def _relative_time(value: Any) -> str:
    if not isinstance(value, datetime):
        return ""
    minutes = max(0, int((datetime.now(UTC) - value.astimezone(UTC)).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    return f"{hours // 24} дн назад"
