from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.message_archive.models import (
    ArchiveAttachment,
    ArchiveAuthor,
    ArchiveChat,
    ArchiveMessage,
)


def _as_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _content_hash(
    text: str | None,
    entities: list[dict[str, Any]],
    *,
    message_type: str,
    forward_info: dict[str, Any] | None,
    attachments: tuple[ArchiveAttachment, ...],
) -> str:
    payload = json.dumps(
        {
            "text": text or "",
            "entities": entities,
            "message_type": message_type,
            "forward_info": forward_info,
            "attachments": [asdict(item) for item in attachments],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Message date is empty")
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw), tz=UTC)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _desktop_text(value: Any) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(value, str):
        return value, []
    if not isinstance(value, list):
        return str(value or ""), []

    chunks: list[str] = []
    entities: list[dict[str, Any]] = []
    offset = 0
    for item in value:
        if isinstance(item, str):
            text = item
            entity_type = None
        elif isinstance(item, dict):
            text = str(item.get("text") or "")
            entity_type = str(item.get("type") or "") or None
        else:
            text = str(item)
            entity_type = None
        chunks.append(text)
        if entity_type and text:
            entities.append({"type": entity_type, "offset": offset, "length": len(text)})
        offset += len(text)
    return "".join(chunks), entities


def _desktop_entities(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return fallback
    if all(isinstance(item, dict) and "offset" in item and "length" in item for item in value):
        return [dict(item) for item in value]
    result: list[dict[str, Any]] = []
    offset = 0
    for item in value:
        if not isinstance(item, dict):
            continue
        fragment = str(item.get("text") or "")
        kind = str(item.get("type") or "")
        if fragment and kind and kind != "plain":
            entity = {"type": kind, "offset": offset, "length": len(fragment)}
            if item.get("href"):
                entity["url"] = item["href"]
            result.append(entity)
        offset += len(fragment)
    return result or fallback


class MessageArchiveNormalizer:
    _DESKTOP_MEDIA_TYPES = {
        "voice_message": "voice",
        "video_message": "video_note",
        "video_file": "video",
        "audio_file": "audio",
        "animation": "animation",
        "sticker": "sticker",
    }

    @classmethod
    def from_bot_message(cls, message: Any, *, outbound: bool) -> ArchiveMessage:
        chat_title = (
            message.chat.title
            or message.chat.full_name
            or message.chat.username
            or str(message.chat.id)
        )
        chat = ArchiveChat(
            title=chat_title,
            chat_type=str(_as_value(message.chat.type)),
            telegram_chat_id=int(message.chat.id),
            username=message.chat.username,
        )

        author = None
        if message.from_user:
            author = ArchiveAuthor(
                display_name=message.from_user.full_name,
                telegram_user_id=int(message.from_user.id),
                username=message.from_user.username,
                is_bot=bool(message.from_user.is_bot),
            )
        elif message.sender_chat:
            author = ArchiveAuthor(
                display_name=message.sender_chat.title
                or message.sender_chat.full_name
                or str(message.sender_chat.id),
                desktop_source_key=f"sender_chat:{message.sender_chat.id}",
                username=message.sender_chat.username,
                metadata={"sender_chat_id": message.sender_chat.id},
            )

        text = message.text if message.text is not None else message.caption
        raw_entities = message.entities if message.text is not None else message.caption_entities
        entities = [item.model_dump(mode="json", exclude_none=True) for item in raw_entities or []]
        attachments = cls._bot_attachments(message)
        message_type = str(_as_value(message.content_type))
        raw_payload = message.model_dump(
            mode="json",
            exclude_none=True,
            exclude={"reply_to_message", "pinned_message"},
            warnings=False,
            # Aiogram may leave Default(...) placeholders in returned messages.
            # Keep the archive JSON-serializable without losing the message itself.
            fallback=str,
        )
        forward_info = cls._bot_forward_info(message)

        return ArchiveMessage(
            chat=chat,
            telegram_message_id=int(message.message_id),
            author=author,
            message_type=message_type,
            direction="outbound" if outbound else "inbound",
            sent_at=_datetime(message.date),
            edited_at=_datetime(message.edit_date) if message.edit_date else None,
            reply_to_message_id=(
                int(message.reply_to_message.message_id) if message.reply_to_message else None
            ),
            media_group_id=str(message.media_group_id) if message.media_group_id else None,
            text_plain=text,
            text_entities=entities,
            forward_info=forward_info,
            raw_payload=raw_payload,
            source="bot_api",
            content_hash=_content_hash(
                text,
                entities,
                message_type=message_type,
                forward_info=forward_info,
                attachments=attachments,
            ),
            attachments=attachments,
        )

    @staticmethod
    def _bot_forward_info(message: Any) -> dict[str, Any] | None:
        """Normalize modern and legacy Bot API forwarding metadata."""
        origin = message.forward_origin
        if origin is not None:
            result = origin.model_dump(mode="json", exclude_none=True)
            # MessageOriginChannel calls this field ``chat``, while chat-origin
            # forwards use ``sender_chat``. Keep one stable key for consumers.
            if isinstance(result.get("chat"), dict) and "sender_chat" not in result:
                result["sender_chat"] = result["chat"]
            if result.get("message_id") is not None:
                result["source_message_id"] = result["message_id"]
            return result

        # Compatibility with updates produced by older Bot API/aiogram versions.
        legacy_result: dict[str, Any] = {}
        forward_from = getattr(message, "forward_from", None)
        forward_from_chat = getattr(message, "forward_from_chat", None)
        if forward_from is not None:
            legacy_result["sender_user"] = forward_from.model_dump(
                mode="json", exclude_none=True
            )
        if forward_from_chat is not None:
            legacy_result["sender_chat"] = forward_from_chat.model_dump(
                mode="json", exclude_none=True
            )
        for source_key, target_key in (
            ("forward_sender_name", "sender_user_name"),
            ("forward_signature", "author_signature"),
            ("forward_from_message_id", "source_message_id"),
        ):
            value = getattr(message, source_key, None)
            if value is not None:
                legacy_result[target_key] = value
        forward_date = getattr(message, "forward_date", None)
        if forward_date is not None:
            legacy_result["date"] = _datetime(forward_date).isoformat()
        if legacy_result:
            legacy_result["type"] = "legacy"
        return legacy_result or None

    @classmethod
    def _bot_attachments(cls, message: Any) -> tuple[ArchiveAttachment, ...]:
        result: list[ArchiveAttachment] = []
        if message.photo:
            photo = message.photo[-1]
            result.append(
                ArchiveAttachment(
                    attachment_type="photo",
                    telegram_file_id=photo.file_id,
                    telegram_file_unique_id=photo.file_unique_id,
                    file_size=photo.file_size,
                    width=photo.width,
                    height=photo.height,
                    download_status="pending",
                    metadata={"variants": len(message.photo)},
                )
            )

        for field, kind in (
            ("voice", "voice"),
            ("video", "video"),
            ("video_note", "video_note"),
            ("audio", "audio"),
            ("document", "document"),
            ("animation", "animation"),
            ("sticker", "sticker"),
        ):
            value = getattr(message, field, None)
            if value is None:
                continue
            result.append(
                ArchiveAttachment(
                    attachment_type=kind,
                    ordinal=0,
                    telegram_file_id=getattr(value, "file_id", None),
                    telegram_file_unique_id=getattr(value, "file_unique_id", None),
                    mime_type=getattr(value, "mime_type", None),
                    original_name=getattr(value, "file_name", None),
                    file_size=getattr(value, "file_size", None),
                    duration_seconds=getattr(value, "duration", None),
                    width=getattr(value, "width", None),
                    height=getattr(value, "height", None),
                    download_status="pending" if kind == "voice" else "skipped",
                    metadata={},
                )
            )
        return tuple(result)

    @classmethod
    def from_desktop_message(
        cls,
        *,
        chat_data: dict[str, Any],
        message_data: dict[str, Any],
        telegram_chat_id: int | None = None,
    ) -> ArchiveMessage:
        desktop_chat_id = int(chat_data["id"])
        chat = ArchiveChat(
            title=str(chat_data.get("name") or desktop_chat_id),
            chat_type=str(chat_data.get("type") or "unknown"),
            telegram_chat_id=telegram_chat_id,
            desktop_chat_id=desktop_chat_id,
            metadata={"desktop_type": chat_data.get("type")},
        )
        author_key = message_data.get("from_id")
        author_name = message_data.get("from") or message_data.get("actor")
        author = None
        if author_key or author_name:
            author = ArchiveAuthor(
                display_name=str(author_name or author_key),
                desktop_source_key=str(author_key) if author_key else None,
                metadata={"actor_id": message_data.get("actor_id")},
            )

        text, entities_from_chunks = _desktop_text(message_data.get("text"))
        entities = _desktop_entities(message_data.get("text_entities"), entities_from_chunks)
        attachments = cls._desktop_attachments(message_data)
        message_type = str(message_data.get("type") or "message")
        forward_info = cls._desktop_forward_info(message_data)
        sent_at = _datetime(message_data.get("date_unixtime") or message_data.get("date"))
        edited_raw = message_data.get("edited_unixtime") or message_data.get("edited")
        return ArchiveMessage(
            chat=chat,
            telegram_message_id=int(message_data["id"]),
            author=author,
            message_type=message_type,
            direction="imported",
            sent_at=sent_at,
            edited_at=_datetime(edited_raw) if edited_raw else None,
            reply_to_message_id=cls._optional_int(message_data.get("reply_to_message_id")),
            media_group_id=(
                str(message_data["media_group_id"])
                if message_data.get("media_group_id") is not None
                else None
            ),
            text_plain=text or None,
            text_entities=entities,
            forward_info=forward_info,
            raw_payload=message_data,
            source="telegram_desktop",
            content_hash=_content_hash(
                text,
                entities,
                message_type=message_type,
                forward_info=forward_info,
                attachments=attachments,
            ),
            attachments=attachments,
        )

    @classmethod
    def _desktop_attachments(
        cls, message: dict[str, Any]
    ) -> tuple[ArchiveAttachment, ...]:
        result: list[ArchiveAttachment] = []
        photo_path = message.get("photo")
        if photo_path:
            result.append(
                ArchiveAttachment(
                    attachment_type="photo",
                    source_path=str(photo_path),
                    file_size=cls._optional_int(message.get("photo_file_size")),
                    width=cls._optional_int(message.get("width")),
                    height=cls._optional_int(message.get("height")),
                    download_status="pending",
                )
            )

        file_path = message.get("file")
        media_type = str(message.get("media_type") or "")
        if file_path or media_type:
            kind = cls._DESKTOP_MEDIA_TYPES.get(media_type, "document")
            result.append(
                ArchiveAttachment(
                    attachment_type=kind,
                    ordinal=0,
                    source_path=str(file_path) if file_path else None,
                    mime_type=message.get("mime_type"),
                    original_name=(Path(str(file_path)).name if file_path else None),
                    file_size=cls._optional_int(message.get("file_size")),
                    duration_seconds=cls._optional_int(message.get("duration_seconds")),
                    width=cls._optional_int(message.get("width")),
                    height=cls._optional_int(message.get("height")),
                    download_status="pending" if kind == "voice" and file_path else "skipped",
                    metadata={"desktop_media_type": media_type},
                )
            )
        return tuple(result)

    @staticmethod
    def _desktop_forward_info(message: dict[str, Any]) -> dict[str, Any] | None:
        result = {
            key: message[key]
            for key in ("forwarded_from", "saved_from", "via_bot")
            if message.get(key) is not None
        }
        return result or None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            match = re.search(r"-?\d+", str(value))
            return int(match.group()) if match else None
