from __future__ import annotations

from typing import Any, Protocol


class ClientRepositoryPort(Protocol):
    async def ensure_client(
        self,
        chat_id: int,
        name: str,
        client_group: str | None = None,
    ) -> int: ...

    async def remove_client(self, chat_id: int) -> bool: ...

    async def list_clients(self) -> list[dict]: ...

    async def list_clients_by_group(self, client_group: str) -> list[dict]: ...

    async def set_client_group_by_chat_id(
        self,
        chat_id: int,
        client_group: str,
    ) -> dict | None: ...

    async def update_client_chat_id(self, *, client_id: int, new_chat_id: int) -> None: ...

    async def find_client_by_name_exact(self, name: str) -> dict[str, Any] | None: ...


class WalletRepositoryPort(Protocol):
    async def add_currency(self, client_id: int, currency_code: str, precision: int) -> int: ...

    async def remove_currency(self, client_id: int, currency_code: str) -> bool: ...

    async def snapshot_wallet(self, client_id: int) -> list[dict[str, Any]]: ...

    async def balances_by_client(self) -> list[dict[str, Any]]: ...
