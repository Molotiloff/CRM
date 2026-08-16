from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from api.schemas.deals import (
    DealDetailsResponse,
    DealItemDto,
    DealLegDto,
    DealsPageResponse,
    DealStatus,
    DealStatusEventDto,
    DealStatusSummaryDto,
)
from domain import Deal
from services.crm.deal_status_policy import DealStatusPolicy

from .common import float_value


def build_deals_page(rows: list[Deal]) -> DealsPageResponse:
    deals = [_deal_item(row) for row in rows]
    summaries = []
    for status in DealStatus:
        status_deals = [deal for deal in deals if deal.status == status]
        summaries.append(
            DealStatusSummaryDto(
                status=status,
                count=len(status_deals),
                totalRub=sum((deal.amountRub for deal in status_deals), 0.0),
            )
        )
    return DealsPageResponse(
        summaries=summaries,
        cities=sorted({deal.city for deal in deals}),
        deals=deals,
    )


def build_deal_details(row: Deal) -> DealDetailsResponse:
    return DealDetailsResponse(
        deal=_deal_item(row),
        dealNo=str(row.deal_no),
        createdAt=_iso(row.created_at),
        updatedAt=_iso(row.updated_at),
        source="sheets" if row.source == "import" else str(row.source),
        sourceKind=str(row.source_kind) if row.source_kind else None,
        counterpartyName=row.counterparty_name,
        counterpartyPercent=_optional_float(row.counterparty_percent),
        profitRub=float_value(row.profit),
        comment=row.comment,
        tronscanUrl=row.tronscan_url,
        paymentWatchId=(str(row.payment_watch_id) if row.payment_watch_id is not None else None),
        paymentWatchStatus=row.payment_watch_status,
        body=row.body.to_dict(),
        legs=[
            DealLegDto(
                id=str(leg.id),
                direction=leg.direction,
                currency=leg.currency_code,
                amount=float_value(leg.amount),
                rate=_optional_float(leg.rate),
                status=leg.status,
            )
            for leg in row.legs
        ],
        statusEvents=[
            DealStatusEventDto(
                id=str(event.id),
                oldStatus=event.old_status,
                newStatus=event.new_status,
                actorName=event.actor_name,
                createdAt=_iso(event.created_at),
                comment=event.payload.get("comment"),
                payload=event.payload.to_dict(),
            )
            for event in row.status_events
        ],
    )


def _deal_item(row: Deal) -> DealItemDto:
    body = row.body.to_dict()
    client_name = row.client_name or "Без клиента"
    status = DealStatus(str(row.status))
    return DealItemDto(
        id=str(row.id),
        clientName=client_name,
        clientShortName=_short_name(client_name),
        dealType=_deal_type_label(str(row.deal_type)),
        asset=_deal_asset(body),
        amountRub=_deal_amount_rub(body),
        city=str(row.city),
        status=status,
        insufficientUsdt=bool(body.get("insufficient_usdt")) or None,
        updatedLabel=_relative_time(row.updated_at),
        createdBy=row.created_by_name,
        onKanban=status not in {DealStatus.done, DealStatus.canceled},
        allowedNextStatuses=[
            DealStatus(item) for item in DealStatusPolicy.allowed_transitions(row)
        ],
    )


def _deal_type_label(deal_type: str) -> str:
    return {
        "sale": "Продажа",
        "purchase": "Покупка",
        "deposit": "Внесение",
        "withdrawal": "Выдача",
        "delivery": "Доставка",
        "transfer_city": "Перестановка",
        "conversion": "Конвертация",
        "yuan": "Юань",
        "invoice": "Инвойс",
        "profit": "Прибыль",
    }.get(deal_type, deal_type)


def _deal_asset(body: Mapping[str, Any]) -> str:
    for key in ("currency", "asset", "from_currency", "fromCurrency"):
        value = str(body.get(key) or "").strip()
        if value:
            return value.upper()
    return "RUB"


def _deal_amount_rub(body: Mapping[str, Any]) -> float:
    for key in (
        "rub_amount",
        "amount_rub",
        "amountRub",
        "sale_amount",
        "buy_amount",
        "clientAmount",
        "amount",
    ):
        value = body.get(key)
        if value is not None:
            try:
                return float(Decimal(str(value)))
            except (ArithmeticError, ValueError):
                continue
    return 0.0


def _short_name(name: str) -> str:
    parts = name.split()
    return name if len(parts) < 2 else f"{parts[0]} {parts[1][0]}."


def _optional_float(value: Any) -> float | None:
    return None if value is None else float_value(value)


def _iso(value: Any) -> str:
    return value.astimezone(UTC).isoformat() if isinstance(value, datetime) else ""


def _relative_time(value: Any) -> str:
    if not isinstance(value, datetime):
        return ""
    minutes = max(0, int((datetime.now(UTC) - value.astimezone(UTC)).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    return f"{hours} ч назад" if hours < 24 else f"{hours // 24} дн назад"
