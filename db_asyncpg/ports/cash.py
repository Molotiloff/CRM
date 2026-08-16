from __future__ import annotations

from typing import Any, Protocol


class RequestScheduleRepositoryPort(Protocol):
    async def next_request_id(self) -> int: ...

    async def upsert_request_schedule_entry(
        self,
        *,
        req_id: str,
        city: str,
        hhmm: str | None,
        request_kind: str,
        line_text: str,
        client_name: str,
        request_chat_id: int,
        request_message_id: int,
    ) -> None: ...

    async def list_request_schedule_entries(self, *, city: str) -> list[dict[str, Any]]: ...

    async def deactivate_request_schedule_entry(self, req_id: str) -> bool: ...

    async def get_request_schedule_board(self, *, city: str) -> dict[str, Any] | None: ...

    async def upsert_request_schedule_board(
        self,
        *,
        city: str,
        board_chat_id: int,
        board_message_id: int,
    ) -> None: ...

    async def deactivate_request_schedule_entry_by_message(
        self,
        *,
        request_chat_id: int,
        request_message_id: int,
    ) -> bool: ...

    async def get_request_schedule_entry_by_message(
        self,
        *,
        request_chat_id: int,
        request_message_id: int,
    ) -> dict[str, Any] | None: ...

    async def get_request_schedule_entry_by_req_id(
        self,
        *,
        req_id: str,
    ) -> dict[str, Any] | None: ...
