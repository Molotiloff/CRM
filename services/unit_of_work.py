from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING, Protocol, Self

from db_asyncpg.ports.cash import RequestScheduleRepositoryPort
from db_asyncpg.ports.exchange import ExchangeRequestRepositoryPort
from db_asyncpg.ports.ledger import TransactionRepositoryPort

if TYPE_CHECKING:
    from db_asyncpg.ports.payment_watch import (
        PaymentWatchRepositoryPort,
        SettlementRepositoryPort,
    )
    from services.accounting.ports import (
        CashSettlementRepositoryPort,
        FirmPositionRepositoryPort,
        FulfillmentQueueRepositoryPort,
        WalletFactRepositoryPort,
    )
    from services.crm.deal_service import DealRepositoryPort


class UnitOfWorkPort(Protocol):
    transactions: TransactionRepositoryPort
    exchange_requests: ExchangeRequestRepositoryPort
    request_schedule: RequestScheduleRepositoryPort
    deals: DealRepositoryPort
    firm_positions: FirmPositionRepositoryPort
    wallet_facts: WalletFactRepositoryPort
    settlements: SettlementRepositoryPort
    fulfillment_queue: FulfillmentQueueRepositoryPort
    payment_watches: PaymentWatchRepositoryPort
    cash_settlements: CashSettlementRepositoryPort

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
