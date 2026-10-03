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
        AccountingImportRepositoryPort,
        CashSettlementRepositoryPort,
        FirmPositionRepositoryPort,
        FulfillmentQueueRepositoryPort,
        ManualCashRepositoryPort,
        PartnerAllocationRepositoryPort,
        ProfitAccrualRepositoryPort,
        WalletFactRepositoryPort,
    )
    from services.best_change.service import BestChangeRepositoryPort
    from services.crm.deal_service import DealRepositoryPort


class UnitOfWorkPort(Protocol):
    @property
    def transactions(self) -> TransactionRepositoryPort: ...
    @property
    def exchange_requests(self) -> ExchangeRequestRepositoryPort: ...
    @property
    def request_schedule(self) -> RequestScheduleRepositoryPort: ...
    @property
    def deals(self) -> DealRepositoryPort: ...
    @property
    def firm_positions(self) -> FirmPositionRepositoryPort: ...
    @property
    def wallet_facts(self) -> WalletFactRepositoryPort: ...
    @property
    def settlements(self) -> SettlementRepositoryPort: ...
    @property
    def fulfillment_queue(self) -> FulfillmentQueueRepositoryPort: ...
    @property
    def payment_watches(self) -> PaymentWatchRepositoryPort: ...
    @property
    def cash_settlements(self) -> CashSettlementRepositoryPort: ...
    @property
    def partner_allocations(self) -> PartnerAllocationRepositoryPort: ...
    @property
    def profit_accruals(self) -> ProfitAccrualRepositoryPort: ...
    @property
    def accounting_imports(self) -> AccountingImportRepositoryPort: ...
    @property
    def best_change(self) -> BestChangeRepositoryPort: ...
    @property
    def manual_cash(self) -> ManualCashRepositoryPort: ...

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
