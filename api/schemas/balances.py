from __future__ import annotations

from pydantic import BaseModel


class BalanceClientDto(BaseModel):
    id: str
    name: str
    clientNumber: str
    currency: str
    balance: float
    balanceRub: float
    initials: str
    telegramChatId: str | None = None


class CurrencySummaryDto(BaseModel):
    code: str
    label: str
    value: float
    changePercent: float | None = None
    tone: str


class BalancesSnapshotResponse(BaseModel):
    clients: list[BalanceClientDto]
    summaries: list[CurrencySummaryDto]
