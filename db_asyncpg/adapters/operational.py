from __future__ import annotations

from datetime import date, datetime
from typing import Any

from db_asyncpg.repositories import (
    ActCounterRepo,
    ClientsRepo,
    ManagersRepo,
    RequestScheduleRepo,
    TransactionsRepo,
)


class ClientWalletTransactionRepositoryAdapter:
    """Explicit workflow view over client/wallet and transaction repositories."""

    def __init__(self, clients: ClientsRepo, transactions: TransactionsRepo) -> None:
        self._clients = clients
        self._transactions = transactions

    async def ensure_client(self, chat_id: int, name: str, client_group: str | None = None) -> int:
        return await self._clients.ensure_client(chat_id, name, client_group)

    async def remove_client(self, chat_id: int) -> bool:
        return await self._clients.remove_client(chat_id)

    async def list_clients(self) -> list[dict]:
        return await self._clients.list_clients()

    async def list_clients_by_group(self, client_group: str) -> list[dict]:
        return await self._clients.list_clients_by_group(client_group)

    async def set_client_group_by_chat_id(self, chat_id: int, client_group: str) -> dict | None:
        return await self._clients.set_client_group_by_chat_id(chat_id, client_group)

    async def update_client_chat_id(self, *, client_id: int, new_chat_id: int) -> None:
        await self._clients.update_client_chat_id(client_id=client_id, new_chat_id=new_chat_id)

    async def find_client_by_name_exact(self, name: str) -> dict[str, Any] | None:
        return await self._clients.find_client_by_name_exact(name)

    async def add_currency(self, client_id: int, currency_code: str, precision: int) -> int:
        return await self._clients.add_currency(client_id, currency_code, precision)

    async def remove_currency(self, client_id: int, currency_code: str) -> bool:
        return await self._clients.remove_currency(client_id, currency_code)

    async def snapshot_wallet(self, client_id: int) -> list[dict[str, Any]]:
        return await self._clients.snapshot_wallet(client_id)

    async def balances_by_client(self) -> list[dict[str, Any]]:
        return await self._clients.balances_by_client()

    async def deposit(self, **kwargs) -> int:
        return await self._transactions.deposit(**kwargs)

    async def withdraw(self, **kwargs) -> int:
        return await self._transactions.withdraw(**kwargs)

    async def get_transaction_by_idempotency_key(
        self, *, client_id: int, idempotency_key: str
    ) -> dict[str, Any] | None:
        return await self._transactions.get_transaction_by_idempotency_key(
            client_id=client_id, idempotency_key=idempotency_key
        )

    async def history(
        self,
        account_id: int,
        *,
        limit: int = 50,
        since: str | datetime | None = None,
        until: str | datetime | None = None,
        cursor_txn_at: str | None = None,
        cursor_id: int | None = None,
    ) -> list[dict[str, Any]]:
        return await self._transactions.history(
            account_id,
            limit=limit,
            since=since,
            until=until,
            cursor_txn_at=cursor_txn_at,
            cursor_id=cursor_id,
        )

    async def export_transactions(
        self,
        *,
        client_id: int | None = None,
        since: datetime | date | str | None = None,
        until: datetime | date | str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._transactions.export_transactions(
            client_id=client_id, since=since, until=until
        )


class ManagedClientWalletTransactionRepositoryAdapter(ClientWalletTransactionRepositoryAdapter):
    def __init__(
        self,
        clients: ClientsRepo,
        transactions: TransactionsRepo,
        managers: ManagersRepo,
    ) -> None:
        super().__init__(clients, transactions)
        self._managers = managers

    async def list_managers(self) -> list[dict]:
        return await self._managers.list_managers()

    async def add_manager(self, user_id: int, display_name: str = "") -> bool:
        return await self._managers.add_manager(user_id, display_name)

    async def remove_manager(self, user_id: int) -> bool:
        return await self._managers.remove_manager(user_id)

    async def is_manager(self, user_id: int) -> bool:
        return await self._managers.is_manager(user_id)


class ClientWalletScheduleContextAdapter:
    """Minimal persistence view shared by cash and exchange command preparation."""

    def __init__(self, clients: ClientsRepo, schedule: RequestScheduleRepo) -> None:
        self._clients = clients
        self._schedule = schedule

    async def ensure_client(self, chat_id: int, name: str, client_group: str | None = None) -> int:
        return await self._clients.ensure_client(chat_id, name, client_group)

    async def snapshot_wallet(self, client_id: int) -> list[dict[str, Any]]:
        return await self._clients.snapshot_wallet(client_id)

    async def next_request_id(self) -> int:
        return await self._schedule.next_request_id()

    async def get_request_schedule_entry_by_req_id(self, *, req_id: str) -> dict[str, Any] | None:
        return await self._schedule.get_request_schedule_entry_by_req_id(req_id=req_id)


class ActCounterLedgerRepositoryAdapter(ClientWalletTransactionRepositoryAdapter):
    def __init__(
        self,
        clients: ClientsRepo,
        transactions: TransactionsRepo,
        act_counter: ActCounterRepo,
    ) -> None:
        super().__init__(clients, transactions)
        self._act_counter = act_counter

    async def link_act_request_transaction(self, **kwargs) -> int:
        return await self._act_counter.link_act_request_transaction(**kwargs)

    async def cancel_act_request_transactions(self, *, req_id: str) -> int:
        return await self._act_counter.cancel_act_request_transactions(req_id=req_id)

    async def get_act_request_transaction(self, *, req_id: str) -> list[dict[str, Any]]:
        return await self._act_counter.get_act_request_transaction(req_id=req_id)
