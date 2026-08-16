from __future__ import annotations

from typing import Any, Protocol

from .administration import ManagerRepositoryPort
from .clients import ClientRepositoryPort, WalletRepositoryPort
from .ledger import ActCounterRepositoryPort, TransactionRepositoryPort


class ClientWalletRepositoryPort(ClientRepositoryPort, WalletRepositoryPort, Protocol):
    pass


class ClientWalletTransactionRepositoryPort(
    ClientRepositoryPort,
    WalletRepositoryPort,
    TransactionRepositoryPort,
    Protocol,
):
    pass


class CashRequestContextRepositoryPort(Protocol):
    async def ensure_client(
        self,
        chat_id: int,
        name: str,
        client_group: str | None = None,
    ) -> int: ...

    async def snapshot_wallet(self, client_id: int) -> list[dict[str, Any]]: ...

    async def get_request_schedule_entry_by_req_id(
        self, *, req_id: str
    ) -> dict[str, Any] | None: ...


class ExchangeCommandRepositoryPort(Protocol):
    async def ensure_client(
        self,
        chat_id: int,
        name: str,
        client_group: str | None = None,
    ) -> int: ...

    async def snapshot_wallet(self, client_id: int) -> list[dict[str, Any]]: ...

    async def next_request_id(self) -> int: ...


class ClientTransactionRepositoryPort(
    ClientRepositoryPort,
    TransactionRepositoryPort,
    Protocol,
):
    pass


class ManagedClientWalletTransactionRepositoryPort(
    ManagerRepositoryPort,
    ClientWalletTransactionRepositoryPort,
    Protocol,
):
    pass


class ClientTransferRepositoryPort(ClientWalletTransactionRepositoryPort, Protocol):
    pass


class ActCounterLedgerRepositoryPort(
    ActCounterRepositoryPort,
    ClientWalletTransactionRepositoryPort,
    Protocol,
):
    pass
