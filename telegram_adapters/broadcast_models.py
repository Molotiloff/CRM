from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import MessageEntity


@dataclass(slots=True, frozen=True)
class TextBroadcastPayload:
    text: str
    entities: tuple[MessageEntity, ...]
    group: str | None


@dataclass(slots=True, frozen=True)
class PhotoBroadcastPayload:
    file_id: str
    caption: str | None
    caption_entities: tuple[MessageEntity, ...]
    group: str | None


@dataclass(slots=True, frozen=True)
class MediaGroupBroadcastPayload:
    file_ids: tuple[str, ...]
    caption: str | None
    caption_entities: tuple[MessageEntity, ...]
    group: str | None


BroadcastPayload = (
    TextBroadcastPayload
    | PhotoBroadcastPayload
    | MediaGroupBroadcastPayload
)

