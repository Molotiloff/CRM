from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

ArchiveSource = Literal["bot_api", "telegram_desktop"]
MessageDirection = Literal["inbound", "outbound", "imported"]
DownloadStatus = Literal["pending", "ready", "skipped", "failed", "unavailable"]


@dataclass(frozen=True, slots=True)
class ArchiveChat:
    title: str
    chat_type: str
    telegram_chat_id: int | None = None
    desktop_chat_id: int | None = None
    username: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArchiveAuthor:
    display_name: str
    telegram_user_id: int | None = None
    desktop_source_key: str | None = None
    username: str | None = None
    is_bot: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArchiveAttachment:
    attachment_type: str
    ordinal: int = 0
    telegram_file_id: str | None = None
    telegram_file_unique_id: str | None = None
    mime_type: str | None = None
    original_name: str | None = None
    file_size: int | None = None
    duration_seconds: int | None = None
    width: int | None = None
    height: int | None = None
    source_path: str | None = None
    download_status: DownloadStatus = "skipped"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArchiveMessage:
    chat: ArchiveChat
    telegram_message_id: int
    sent_at: datetime
    message_type: str
    direction: MessageDirection
    source: ArchiveSource
    content_hash: str
    author: ArchiveAuthor | None = None
    edited_at: datetime | None = None
    reply_to_message_id: int | None = None
    media_group_id: str | None = None
    text_plain: str | None = None
    text_entities: list[dict[str, Any]] = field(default_factory=list)
    forward_info: dict[str, Any] | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
    attachments: tuple[ArchiveAttachment, ...] = ()


@dataclass(frozen=True, slots=True)
class SavedAttachment:
    id: int
    attachment_type: str
    ordinal: int
    telegram_file_id: str | None
    source_path: str | None
    download_status: str


@dataclass(frozen=True, slots=True)
class SaveMessageResult:
    message_id: int
    chat_id: int
    sent_at: datetime
    action: Literal["created", "updated", "skipped"]
    attachments: tuple[SavedAttachment, ...] = ()


@dataclass(frozen=True, slots=True)
class ExportPart:
    path: str
    message_count: int
    size: int
    date_from: datetime | None
    date_to: datetime | None

