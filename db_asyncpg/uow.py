from __future__ import annotations

from types import TracebackType

import asyncpg

from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.repositories.exchange_requests import ExchangeRequestsRepo
from db_asyncpg.repositories.firm_positions import FirmPositionsRepo
from db_asyncpg.repositories.fulfillment_queue import FulfillmentQueueRepo
from db_asyncpg.repositories.payment_watch import PaymentWatchRepo
from db_asyncpg.repositories.request_schedule import RequestScheduleRepo
from db_asyncpg.repositories.settlements import DealSettlementRepo
from db_asyncpg.repositories.transactions import TransactionsRepo
from db_asyncpg.repositories.wallet_facts import WalletFactsRepo


class AsyncpgUnitOfWork:
    """One asyncpg transaction with repositories bound to its connection."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._connection: asyncpg.Connection | None = None
        self._transaction: asyncpg.Transaction | None = None
        self._finished = False
        self.transactions: TransactionsRepo
        self.exchange_requests: ExchangeRequestsRepo
        self.request_schedule: RequestScheduleRepo
        self.deals: DealRepository
        self.firm_positions: FirmPositionsRepo
        self.wallet_facts: WalletFactsRepo
        self.settlements: DealSettlementRepo
        self.fulfillment_queue: FulfillmentQueueRepo
        self.payment_watches: PaymentWatchRepo

    async def __aenter__(self) -> AsyncpgUnitOfWork:
        self._connection = await self._pool.acquire()
        self._transaction = self._connection.transaction()
        await self._transaction.start()
        self.transactions = TransactionsRepo(
            self._pool,
            connection=self._connection,
        )
        self.exchange_requests = ExchangeRequestsRepo(
            self._pool,
            connection=self._connection,
        )
        self.request_schedule = RequestScheduleRepo(
            self._pool,
            connection=self._connection,
        )
        self.deals = DealRepository(
            self._pool,
            connection=self._connection,
        )
        self.firm_positions = FirmPositionsRepo(
            self._pool,
            connection=self._connection,
        )
        self.wallet_facts = WalletFactsRepo(
            self._pool,
            connection=self._connection,
        )
        self.settlements = DealSettlementRepo(
            self._pool,
            connection=self._connection,
        )
        self.fulfillment_queue = FulfillmentQueueRepo(
            self._pool,
            connection=self._connection,
        )
        self.payment_watches = PaymentWatchRepo(
            self._pool,
            connection=self._connection,
        )
        return self

    async def commit(self) -> None:
        if self._transaction is None or self._finished:
            raise RuntimeError("Unit of Work is not active")
        await self._transaction.commit()
        self._finished = True

    async def rollback(self) -> None:
        if self._transaction is None or self._finished:
            return
        await self._transaction.rollback()
        self._finished = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if not self._finished:
                await self.rollback()
        finally:
            if self._connection is not None:
                await self._pool.release(self._connection)
                self._connection = None
                self._transaction = None
