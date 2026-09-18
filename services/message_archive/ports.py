from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from services.message_archive.models import ArchiveMessage, SaveMessageResult


class MessageArchiveRepositoryPort(Protocol):
    async def save_archive_message(self, message: ArchiveMessage) -> SaveMessageResult: ...

    async def mark_archive_attachment_ready(
        self, *, attachment_id: int, storage_key: str, sha256: str, file_size: int
    ) -> None: ...

    async def mark_archive_attachment_failed(
        self, *, attachment_id: int, error: str, unavailable: bool = False
    ) -> None: ...

    async def list_pending_archive_attachments(
        self, *, limit: int = 500
    ) -> list[dict[str, Any]]: ...

    async def search_archive_chats(
        self, query: str, *, limit: int = 20
    ) -> list[dict[str, Any]]: ...

    async def get_archive_chat(self, chat_id: int) -> dict[str, Any] | None: ...

    async def list_archive_messages_page(
        self,
        *,
        chat_id: int,
        cursor_sent_at: datetime | None = None,
        cursor_message_id: int | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    async def list_archive_attachments_for_messages(
        self, message_ids: list[int]
    ) -> list[dict[str, Any]]: ...

    async def get_archive_statistics(self) -> dict[str, int]: ...

    async def create_archive_import(
        self,
        *,
        source_path: str,
        source_checksum: str,
        dry_run: bool,
        summary: dict[str, Any] | None = None,
    ) -> int: ...

    async def finish_archive_import(
        self,
        *,
        import_id: int,
        status: str,
        chats_found: int = 0,
        messages_created: int = 0,
        messages_updated: int = 0,
        messages_skipped: int = 0,
        files_missing: int = 0,
        summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None: ...

    async def create_archive_export(
        self, *, chat_id: int, requested_by_user_id: int, requested_in_chat_id: int
    ) -> int: ...

    async def finish_archive_export(
        self,
        *,
        export_id: int,
        status: str,
        message_count: int = 0,
        part_count: int = 0,
        total_size: int = 0,
        telegram_message_ids: list[int] | None = None,
        error: str | None = None,
    ) -> None: ...
