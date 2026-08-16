from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol


class TransactionRepositoryPort(Protocol):
    async def deposit(self, **kwargs) -> int: ...

    async def withdraw(self, **kwargs) -> int: ...

    async def get_transaction_by_idempotency_key(
        self,
        *,
        client_id: int,
        idempotency_key: str,
    ) -> dict[str, Any] | None: ...

    async def history(
        self,
        account_id: int,
        *,
        limit: int = 50,
        since: str | datetime | None = None,
        until: str | datetime | None = None,
        cursor_txn_at: str | None = None,
        cursor_id: int | None = None,
    ) -> list[dict[str, Any]]: ...

    async def export_transactions(
        self,
        *,
        client_id: int | None = None,
        since: datetime | date | str | None = None,
        until: datetime | date | str | None = None,
    ) -> list[dict[str, Any]]: ...


class ActCounterRepositoryPort(Protocol):
    async def link_act_request_transaction(
        self,
        *,
        req_id: str,
        request_chat_id: int,
        request_message_id: int,
        transaction_id: int,
        direction: str,
        table_req_id: str | None = None,
        status: str = "ACTIVE",
    ) -> int: ...

    async def cancel_act_request_transactions(self, *, req_id: str) -> int: ...

    async def get_act_request_transaction(self, *, req_id: str) -> list[dict[str, Any]]: ...
