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
    client_transfer = "client_transfer"
    conversion = "conversion"
    yuan = "yuan"
    invoice = "invoice"
    profit = "profit"
    best_change = "best_change"


class DealStatus(StrEnum):
    new = "new"
    fixed = "fixed"
    balance_check = "balance_check"
    awaiting_payment = "awaiting_payment"
    in_delivery = "in_delivery"
    ready_for_cash_settlement = "ready_for_cash_settlement"
    done = "done"
    canceled = "canceled"


class SettlementResolution(StrEnum):
    accept_actual = "accept_actual"
    amend_unposted = "amend_unposted"
    cancel_and_recreate = "cancel_and_recreate"


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


class ClientTransferCreateRequest(BaseModel):
    fromClientId: int = Field(gt=0)
    toClientId: int = Field(gt=0)
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=1, max_length=12)
    idempotencyKey: str = Field(min_length=1, max_length=120)
    comment: str | None = None
    allowNegative: bool = False


class DealFormClientDto(BaseModel):
    id: str
    name: str


class DealFormContextDto(BaseModel):
    cities: list[str]
    counterparties: list[dict[str, str]]
    clients: list[DealFormClientDto]
    companyRates: dict[str, float]
    defaultCounterpartyPercent: float


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


class SettlementReplacementRequest(BaseModel):
    requestId: str = Field(min_length=1)
    tableRequestId: str = Field(min_length=1)
    recvCode: str = Field(min_length=1)
    recvAmount: Decimal = Field(gt=0)
    payCode: str = Field(min_length=1)
    payAmount: Decimal = Field(gt=0)
    rate: Decimal = Field(gt=0)


class SettlementReviewResolutionRequest(BaseModel):
    resolution: SettlementResolution
    comment: str | None = None
    replacement: SettlementReplacementRequest | None = None

    @model_validator(mode="after")
    def validate_replacement(self) -> SettlementReviewResolutionRequest:
        requires_replacement = self.resolution is SettlementResolution.cancel_and_recreate
        if requires_replacement != (self.replacement is not None):
            raise ValueError(
                "replacement is required only for cancel_and_recreate"
            )
        return self


class SettlementResolutionResponse(BaseModel):
    settlementId: int
    dealId: int
    status: str
    resolution: SettlementResolution
    expected: Decimal
    actual: Decimal
    delta: Decimal


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
    direction: str
    amountRub: float
    transferAmount: str | None = None
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


class BestChangeDetailsDto(BaseModel):
    operation: str
    qtyUsdt: float | None = None
    marketRateRub: float | None = None
    clientRateRub: float | None = None
    unitSpreadRub: float | None = None
    grossSpreadRub: float | None = None
    profitPoolRub: float | None = None
    partnerShareRub: float | None = None
    skyexProfitRub: float | None = None
    platformFeeUsdt: float | None = None
    originalDealId: str | None = None
    replacementDealId: str | None = None
    correctionReason: str | None = None
    correctionActor: str | None = None


class DealDetailsResponse(BaseModel):
    deal: DealItemDto
    dealNo: str
    createdAt: str
    updatedAt: str
    dealAt: str | None = None
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
    bestChange: BestChangeDetailsDto | None = None
    legs: list[DealLegDto] = Field(default_factory=list)
    statusEvents: list[DealStatusEventDto] = Field(default_factory=list)
