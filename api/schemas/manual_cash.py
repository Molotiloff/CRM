from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class ManualCashAccountDto(BaseModel):
    code: str
    name: str
    balance: str


class ManualCashMoveDto(BaseModel):
    id: int
    accountCode: str
    operation: str
    amount: str
    balanceAfter: str
    effectiveAt: date
    comment: str
    actorName: str
    reversalOfId: int | None
    reversed: bool


class ManualCashSnapshotResponse(BaseModel):
    accounts: list[ManualCashAccountDto]
    moves: list[ManualCashMoveDto]


class ManualCashMoveRequest(BaseModel):
    accountCode: Literal["moscow_poets", "moscow_bs"]
    operation: Literal["inflow", "outflow"]
    amount: Decimal = Field(gt=0)
    effectiveAt: date
    comment: str = Field(min_length=1, max_length=500)
    idempotencyKey: str = Field(min_length=8, max_length=200)


class ManualCashReversalRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=500)
    idempotencyKey: str = Field(min_length=8, max_length=200)
