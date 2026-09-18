from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from services.message_archive.archive_service import MessageArchiveService
from services.message_archive.media_download_service import MediaDownloadService
from services.message_archive.normalizer import MessageArchiveNormalizer
from services.message_archive.telegram_desktop_parser import TelegramDesktopParser


@dataclass(slots=True)
class ImportStatistics:
    chats: set[int]
    created: int = 0
    updated: int = 0
    skipped: int = 0


class TelegramHistoryImportService:
    def __init__(
        self,
        *,
        archive_service: MessageArchiveService,
        media_service: MediaDownloadService,
        parser: TelegramDesktopParser | None = None,
    ) -> None:
        self.archive_service = archive_service
        self.media_service = media_service
        self.parser = parser or TelegramDesktopParser()

    async def import_export(
        self,
        export_path: str | Path,
        *,
        chat_mapping: dict[int, int] | None = None,
    ) -> ImportStatistics:
        source = Path(export_path).expanduser().resolve()
        result_json = source / "result.json" if source.is_dir() else source
        media_root = result_json.parent
        if not result_json.is_file():
            raise FileNotFoundError(result_json)
        mapping = chat_mapping or {}
        stats = ImportStatistics(chats=set())
        for record in self.parser.iter_messages(result_json):
            desktop_chat_id = int(record.chat["id"])
            stats.chats.add(desktop_chat_id)
            normalized = MessageArchiveNormalizer.from_desktop_message(
                chat_data=record.chat,
                message_data=record.message,
                telegram_chat_id=mapping.get(desktop_chat_id),
            )
            result = await self.archive_service.archive(normalized, source_root=str(media_root))
            if result.action == "created":
                stats.created += 1
            elif result.action == "updated":
                stats.updated += 1
            else:
                stats.skipped += 1
        await self.media_service.wait_until_idle()
        return stats

    @staticmethod
    def checksum(result_json: str | Path) -> str:
        digest = hashlib.sha256()
        with Path(result_json).open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

