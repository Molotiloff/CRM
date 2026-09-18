from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from config import Config
from db_asyncpg.pool import close_pool, create_pool
from db_asyncpg.repositories.message_archive import MessageArchiveRepo
from services.message_archive import LocalMediaStorage, MediaDownloadService, MessageArchiveService
from services.message_archive.import_service import TelegramHistoryImportService
from services.message_archive.telegram_desktop_parser import TelegramDesktopParser


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Telegram Desktop history into CRM")
    parser.add_argument("--input", required=True, help="Telegram Desktop export directory or result.json")
    parser.add_argument("--mapping", help="JSON object mapping Desktop chat IDs to Bot API chat IDs")
    parser.add_argument(
        "--allow-unmapped",
        action="store_true",
        help="Explicitly allow importing chats absent from --mapping as standalone archive chats",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def _load_mapping(path: str | None) -> dict[int, int]:
    if not path:
        return {}
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Mapping must be a JSON object")
    return {int(key): int(chat_id) for key, chat_id in value.items()}


def _annotate_mapping(summary: dict, mapping: dict[int, int]) -> dict:
    for chat in summary.get("chats", []):
        desktop_chat_id = int(chat["id"])
        chat["mapped_telegram_chat_id"] = mapping.get(desktop_chat_id)
        chat["mapping_status"] = "manual" if desktop_chat_id in mapping else "unmapped"
    summary["mapping_count"] = len(mapping)
    summary["unmapped_chat_ids"] = [
        int(chat["id"])
        for chat in summary.get("chats", [])
        if chat["mapping_status"] == "unmapped"
    ]
    return summary


async def _apply(args: argparse.Namespace, mapping: dict[int, int]) -> None:
    config = Config.from_env()
    pool = await create_pool(config.database_url)
    repo = MessageArchiveRepo(pool)
    source = Path(args.input).expanduser().resolve()
    result_json = source / "result.json" if source.is_dir() else source
    checksum = TelegramHistoryImportService.checksum(result_json)
    summary = _annotate_mapping(TelegramDesktopParser().summarize(source), mapping)
    if summary["unmapped_chat_ids"] and not args.allow_unmapped:
        missing = ", ".join(str(item) for item in summary["unmapped_chat_ids"])
        raise RuntimeError(
            "Import stopped: chats are absent from --mapping: "
            f"{missing}. Add mappings or explicitly pass --allow-unmapped."
        )
    import_id: int | None = None
    storage = LocalMediaStorage(config.message_archive_media_dir)
    media = MediaDownloadService(
        repo=repo,
        storage=storage,
        workers=config.message_archive_download_workers,
        queue_size=config.message_archive_download_queue_size,
    )
    archive = MessageArchiveService(repo=repo, attachment_scheduler=media)
    importer = TelegramHistoryImportService(archive_service=archive, media_service=media)
    await media.start()
    try:
        import_id = await repo.create_archive_import(
            source_path=str(source),
            source_checksum=checksum,
            dry_run=False,
            summary=summary,
        )
        try:
            stats = await importer.import_export(args.input, chat_mapping=mapping)
        except Exception as exc:
            await repo.finish_archive_import(
                import_id=import_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        await repo.finish_archive_import(
            import_id=import_id,
            status="completed",
            chats_found=len(stats.chats),
            messages_created=stats.created,
            messages_updated=stats.updated,
            messages_skipped=stats.skipped,
            files_missing=int(summary["missing_files"]),
        )
        print(
            json.dumps(
                {
                    "import_id": import_id,
                    "checksum": checksum,
                    "chats": len(stats.chats),
                    "created": stats.created,
                    "updated": stats.updated,
                    "skipped": stats.skipped,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await media.stop()
        await close_pool(pool)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = _arguments()
    mapping = _load_mapping(args.mapping)
    if args.dry_run:
        summary = _annotate_mapping(TelegramDesktopParser().summarize(args.input), mapping)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    asyncio.run(_apply(args, mapping))


if __name__ == "__main__":
    main()
