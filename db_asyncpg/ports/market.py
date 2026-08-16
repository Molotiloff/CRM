from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol


class RateOrderRepositoryPort(Protocol):
    async def create_rate_order(
        self,
        *,
        client_chat_id: int,
        client_name: str,
        requested_rate: Decimal,
        created_by_user_id: int | None,
        order_chat_id: int,
        order_message_id: int,
    ) -> int: ...

    async def set_rate_order_message_binding(
        self,
        *,
        order_id: int,
        order_chat_id: int,
        order_message_id: int,
    ) -> None: ...

    async def get_rate_order_by_message(
        self,
        *,
        order_chat_id: int,
        order_message_id: int,
    ) -> dict[str, Any] | None: ...

    async def get_rate_order_by_id(self, order_id: int) -> dict[str, Any] | None: ...

    async def activate_rate_order(
        self,
        *,
        order_id: int,
        commission: Decimal,
        target_ask: Decimal,
        activated_by_user_id: int | None,
    ) -> None: ...

    async def list_active_rate_orders(self) -> list[dict[str, Any]]: ...

    async def mark_rate_order_triggered(self, *, order_id: int) -> bool: ...


class LiveMessageRepositoryPort(Protocol):
    async def upsert_live_message(
        self,
        *,
        chat_id: int,
        message_key: str,
        message_id: int,
    ) -> None: ...

    async def get_live_message(
        self,
        *,
        chat_id: int,
        message_key: str,
    ) -> dict[str, Any] | None: ...

    async def delete_live_message(self, *, chat_id: int, message_key: str) -> bool: ...

    async def list_live_messages(
        self,
        *,
        message_key: str | None = None,
    ) -> list[dict[str, Any]]: ...
