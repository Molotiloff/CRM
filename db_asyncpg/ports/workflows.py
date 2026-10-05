from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .administration import ManagerRepositoryPort
from .clients import ClientRepositoryPort, WalletRepositoryPort
from .ledger import ActCounterRepositoryPort, TransactionRepositoryPort


@dataclass(frozen=True, slots=True)
class TgOutboxItem:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int
    created_at: datetime


class TgOutboxRepositoryPort(Protocol):
    async def claim_batch(
        self, *, limit: int, max_attempts: int, lock_timeout_seconds: int
    ) -> list[TgOutboxItem]: ...

    async def mark_sent(self, outbox_id: int) -> None: ...

    async def mark_failed(
        self, outbox_id: int, *, error: str, retry: bool, retry_delay_seconds: int
    ) -> None: ...

    async def count_pending(self, *, max_attempts: int) -> int: ...


class TgOutboxDeliveryRepositoryPort(Protocol):
    async def get_deal_delivery_context(self, deal_id: int) -> dict[str, Any] | None: ...

    async def find_archived_deal_status_message(
        self, chat_id: int, request_id: str
    ) -> int | None: ...

    async def get_deal_status_message(self, deal_id: int, chat_id: int) -> int | None: ...

    async def save_deal_status_message(
        self, deal_id: int, chat_id: int, message_id: int
    ) -> None: ...


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

    async def get_cash_request_deal_status(self, *, req_id: str) -> str | None: ...


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
