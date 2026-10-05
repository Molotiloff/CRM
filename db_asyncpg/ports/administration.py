from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol


class ManagerRepositoryPort(Protocol):
    async def list_managers(self) -> list[dict]: ...

    async def add_manager(self, user_id: int, display_name: str = "") -> bool: ...

    async def remove_manager(self, user_id: int) -> bool: ...

    async def is_manager(self, user_id: int) -> bool: ...


class SettingsRepositoryPort(Protocol):
    async def get_setting(self, key: str) -> str | None: ...

    async def set_setting(self, key: str, value: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ClientInviteTarget:
    client_id: int
    chat_id: int


class ClientInviteLinkRepository(Protocol):
    def list_missing(
        self, *, after_client_id: int, limit: int
    ) -> Awaitable[list[ClientInviteTarget]]: ...

    def save_if_missing(self, target: ClientInviteTarget, link: str) -> Awaitable[bool]: ...
