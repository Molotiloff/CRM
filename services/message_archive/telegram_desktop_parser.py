from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DesktopMessageRecord:
    chat: dict[str, Any]
    message: dict[str, Any]


class TelegramDesktopParser:
    """Streaming parser for single-chat and full Telegram Desktop JSON exports."""

    _SINGLE_MESSAGE_PREFIX = "messages.item"
    _FULL_MESSAGE_PREFIX = "chats.list.item.messages.item"

    def iter_messages(self, result_json: str | Path) -> Iterator[DesktopMessageRecord]:
        try:
            import ijson
            from ijson.common import ObjectBuilder
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("Install runtime dependency 'ijson' to import Telegram exports") from exc

        current_single: dict[str, Any] = {}
        current_full: dict[str, Any] = {}
        builder = None
        active_prefix: str | None = None

        with Path(result_json).open("rb") as source:
            for prefix, event, value in ijson.parse(source):
                if prefix in {"id", "name", "type"} and event in {"string", "number"}:
                    current_single[prefix] = value
                elif prefix in {
                    "chats.list.item.id",
                    "chats.list.item.name",
                    "chats.list.item.type",
                } and event in {"string", "number"}:
                    current_full[prefix.rsplit(".", 1)[-1]] = value

                is_message_root = prefix in {
                    self._SINGLE_MESSAGE_PREFIX,
                    self._FULL_MESSAGE_PREFIX,
                }
                if is_message_root and event == "start_map":
                    builder = ObjectBuilder()
                    active_prefix = prefix

                if builder is not None and active_prefix is not None:
                    if prefix == active_prefix or prefix.startswith(f"{active_prefix}."):
                        builder.event(event, value)
                    if prefix == active_prefix and event == "end_map":
                        chat = current_full if active_prefix == self._FULL_MESSAGE_PREFIX else current_single
                        if not {"id", "name", "type"}.issubset(chat):
                            raise ValueError("Telegram export message appeared before chat metadata")
                        yield DesktopMessageRecord(chat=dict(chat), message=dict(builder.value))
                        builder = None
                        active_prefix = None

                if prefix == "chats.list.item" and event == "end_map":
                    current_full = {}

    def summarize(self, export_root: str | Path) -> dict[str, Any]:
        root = Path(export_root).expanduser().resolve()
        result_json = root / "result.json" if root.is_dir() else root
        media_root = result_json.parent
        chats: dict[int, dict[str, Any]] = {}
        messages = 0
        photos = 0
        voices = 0
        missing_files = 0
        photo_bytes = 0
        voice_bytes = 0
        metadata_only_attachments = 0
        for record in self.iter_messages(result_json):
            chat_id = int(record.chat["id"])
            item = chats.setdefault(
                chat_id,
                {"id": chat_id, "name": str(record.chat["name"]), "messages": 0},
            )
            item["messages"] += 1
            messages += 1
            if record.message.get("photo"):
                photos += 1
                photo_bytes += self._safe_int(record.message.get("photo_file_size"))
                if not self._safe_media_exists(media_root, str(record.message["photo"])):
                    missing_files += 1
            if record.message.get("media_type") == "voice_message":
                voices += 1
                voice_bytes += self._safe_int(record.message.get("file_size"))
                file_path = record.message.get("file")
                if file_path and not self._safe_media_exists(media_root, str(file_path)):
                    missing_files += 1
            elif record.message.get("file") or record.message.get("media_type"):
                metadata_only_attachments += 1
        return {
            "chats": list(chats.values()),
            "chat_count": len(chats),
            "message_count": messages,
            "photo_count": photos,
            "voice_count": voices,
            "photo_bytes": photo_bytes,
            "voice_bytes": voice_bytes,
            "media_bytes": photo_bytes + voice_bytes,
            "metadata_only_attachment_count": metadata_only_attachments,
            "missing_files": missing_files,
        }

    @staticmethod
    def _safe_media_exists(root: Path, relative: str) -> bool:
        path = (root / relative).resolve()
        return path.is_relative_to(root) and path.is_file()

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

