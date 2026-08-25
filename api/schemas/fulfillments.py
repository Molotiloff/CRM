from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FulfillmentQueueItemResponse(BaseModel):
    id: int
    dealId: int
    requestKind: str
    qty: str
    sequenceNo: int
    status: str
    createdBy: int | None
    createdAt: datetime
    reorderedBy: int | None
    reorderedAt: datetime | None


class FulfillmentQueueResponse(BaseModel):
    items: list[FulfillmentQueueItemResponse]
    queuedQty: str
    usdtFact: str | None
    queueShortageQty: str | None
    onchainLiquidQty: str | None


class FulfillmentReorderRequest(BaseModel):
    beforeItemId: int | None = Field(default=None, gt=0)


class FulfillmentCancelRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
