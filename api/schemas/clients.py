from __future__ import annotations

from pydantic import BaseModel, Field


class ClientBalanceDto(BaseModel):
    currency: str
    amount: float


class ClientCommentDto(BaseModel):
    id: str
    date: str
    author: str
    text: str


class ClientRecentDealDto(BaseModel):
    id: str
    type: str
    direction: str
    amountRub: float
    time: str


class ClientDto(BaseModel):
    id: str
    clientNumber: str
    name: str
    initials: str
    telegramUsername: str
    telegramChatId: str
    dealsCount: int
    turnoverRub: float
    managerName: str
    registrationDate: str
    counterpartyName: str | None = None
    counterpartyPercent: float | None = None
    comment: str | None = None
    balances: list[ClientBalanceDto] = Field(default_factory=list)
    totalProfitRub: float
    purchaseVolumeRub: float
    saleVolumeRub: float
    averageCheckRub: float
    recentDeals: list[ClientRecentDealDto] = Field(default_factory=list)
    comments: list[ClientCommentDto] = Field(default_factory=list)


class ClientMetricDto(BaseModel):
    id: str
    title: str
    value: str | int | float
    changePercent: float
    subtitle: str
    tone: str
    icon: str


class ClientsPageResponse(BaseModel):
    metrics: list[ClientMetricDto]
    clients: list[ClientDto]
    totalClients: int


class ClientTransactionDto(BaseModel):
    id: str
    txnAt: str
    currency: str
    amount: float
    balanceAfter: float
    comment: str | None = None
    source: str | None = None
    groupName: str | None = None
    actorName: str | None = None


class ClientTransactionsResponse(BaseModel):
    items: list[ClientTransactionDto]
    limit: int
