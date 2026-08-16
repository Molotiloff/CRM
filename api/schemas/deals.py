from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class DealType(StrEnum):
    sale = "sale"
    purchase = "purchase"
    deposit = "deposit"
    withdrawal = "withdrawal"
    delivery = "delivery"
    transfer_city = "transfer_city"
    conversion = "conversion"
    yuan = "yuan"
    invoice = "invoice"
    profit = "profit"


class DealStatus(StrEnum):
    new = "new"
    fixed = "fixed"
    balance_check = "balance_check"
    awaiting_payment = "awaiting_payment"
    in_delivery = "in_delivery"
    done = "done"
    canceled = "canceled"


class DealCreateRequest(BaseModel):
    dealType: DealType
    city: str
    clientId: int | None = None
    counterpartyId: int | None = None
    comment: str | None = None
    tronscanUrl: str | None = None
    body: dict[str, Any] = Field(default_factory=dict)
    profitRub: Decimal | None = None
    dealAt: date | None = None
    exchangeClientReqId: str | None = None


class DealUpdateRequest(BaseModel):
    city: str | None = None
    clientId: int | None = None
    counterpartyId: int | None = None
    comment: str | None = None
    tronscanUrl: str | None = None
    body: dict[str, Any] | None = None
    profitRub: Decimal | None = None
    dealAt: date | None = None


class DealStatusRequest(BaseModel):
    status: DealStatus
    comment: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class DealCancelRequest(BaseModel):
    comment: str | None = None


class ExchangeSourceEditRequest(BaseModel):
    operationId: int = Field(gt=0)
    recvCode: str = Field(min_length=1)
    recvAmount: Decimal = Field(gt=0)
    payCode: str = Field(min_length=1)
    payAmount: Decimal = Field(gt=0)
    rate: Decimal = Field(gt=0)
    note: str | None = None


class CashSourceEditRequest(BaseModel):
    city: str = Field(min_length=1)
    amount: Decimal | None = Field(default=None, gt=0)
    inAmount: Decimal | None = Field(default=None, gt=0)
    outAmount: Decimal | None = Field(default=None, gt=0)
    comment: str | None = None
    contact1: str = ""
    contact2: str = ""


class DealSourceEditRequest(BaseModel):
    exchange: ExchangeSourceEditRequest | None = None
    cash: CashSourceEditRequest | None = None

    @model_validator(mode="after")
    def exactly_one_source(self) -> DealSourceEditRequest:
        if (self.exchange is None) == (self.cash is None):
            raise ValueError("Exactly one of exchange or cash must be provided")
        return self


class DealItemDto(BaseModel):
    id: str
    clientName: str
    clientShortName: str
    dealType: str
    asset: str
    amountRub: float
    city: str
    status: DealStatus
    insufficientUsdt: bool | None = None
    updatedLabel: str
    createdBy: str
    onKanban: bool
    allowedNextStatuses: list[DealStatus] = Field(default_factory=list)


class DealStatusSummaryDto(BaseModel):
    status: DealStatus
    count: int
    totalRub: float | None


class DealsPageResponse(BaseModel):
    summaries: list[DealStatusSummaryDto]
    cities: list[str]
    deals: list[DealItemDto]


class DealLegDto(BaseModel):
    id: str
    direction: str
    currency: str
    amount: float
    rate: float | None = None
    status: str


class DealStatusEventDto(BaseModel):
    id: str
    oldStatus: DealStatus | None
    newStatus: DealStatus
    actorName: str
    createdAt: str
    comment: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class DealDetailsResponse(BaseModel):
    deal: DealItemDto
    dealNo: str
    createdAt: str
    updatedAt: str
    source: str
    sourceKind: str | None = None
    counterpartyName: str | None = None
    counterpartyPercent: float | None = None
    profitRub: float
    comment: str | None = None
    tronscanUrl: str | None = None
    paymentWatchId: str | None = None
    paymentWatchStatus: str | None = None
    body: dict[str, Any] = Field(default_factory=dict)
    legs: list[DealLegDto] = Field(default_factory=list)
    statusEvents: list[DealStatusEventDto] = Field(default_factory=list)
