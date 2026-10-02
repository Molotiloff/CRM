from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol


class ExchangeRequestRepositoryPort(Protocol):
    def table_write_lock(self, table_req_id: str) -> AbstractAsyncContextManager[None]: ...

    async def claim_exchange_request_cancellation(self, *, client_req_id: str) -> bool: ...

    async def upsert_exchange_request_link(
        self,
        *,
        client_req_id: str,
        table_req_id: str,
        client_chat_id: int | None = None,
        client_message_id: int | None = None,
        request_chat_id: int | None = None,
        request_message_id: int | None = None,
        request_text: str | None = None,
        table_in_cur: str | None = None,
        table_out_cur: str | None = None,
        table_in_amount: Any | None = None,
        table_out_amount: Any | None = None,
        table_rate: Any | None = None,
        is_table_done: bool | None = None,
        status: str | None = None,
    ) -> None: ...

    async def get_exchange_request_link(
        self,
        *,
        client_req_id: str,
    ) -> dict[str, Any] | None: ...

    async def get_exchange_request_link_by_table_req_id(
        self,
        *,
        table_req_id: str,
    ) -> dict[str, Any] | None: ...

    async def mark_exchange_request_table_done(
        self,
        *,
        table_req_id: str,
        is_table_done: bool = True,
    ) -> bool: ...

    async def set_exchange_request_status(
        self,
        *,
        client_req_id: str,
        status: str,
    ) -> bool: ...
