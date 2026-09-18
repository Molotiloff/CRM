from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from services.message_archive.export_service import MessageExportService
from services.message_archive.media_storage import LocalMediaStorage


class SyntheticArchiveRepository:
    def __init__(self, message_count: int, text_size: int) -> None:
        self.message_count = message_count
        self.text = "x" * text_size
        self.started_at = datetime(2020, 1, 1, tzinfo=UTC)

    async def get_archive_chat(self, chat_id: int):
        return {
            "id": chat_id,
            "current_title": "Archive benchmark",
            "telegram_chat_id": -1001,
        }

    async def list_archive_messages_page(
        self,
        *,
        chat_id: int,
        cursor_sent_at=None,
        cursor_message_id=None,
        limit=500,
    ):
        first_id = int(cursor_message_id or 0) + 1
        last_id = min(self.message_count, first_id + limit - 1)
        return [self._message(message_id) for message_id in range(first_id, last_id + 1)]

    async def list_archive_attachments_for_messages(self, message_ids):
        return []

    def _message(self, message_id: int) -> dict:
        return {
            "id": message_id,
            "telegram_message_id": message_id,
            "sent_at": self.started_at + timedelta(seconds=message_id),
            "message_type": "message",
            "direction": "inbound",
            "text_plain": self.text,
            "text_entities": [],
            "source": "bot_api",
            "edited_at": None,
            "author_name": "Load test",
        }


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


async def _run(args: argparse.Namespace) -> dict:
    with TemporaryDirectory(prefix="message-archive-benchmark-") as temp_dir:
        service = MessageExportService(
            repo=SyntheticArchiveRepository(args.messages, args.text_size),
            storage=LocalMediaStorage(Path(temp_dir) / "media"),
            temp_dir=Path(temp_dir) / "tmp",
            part_size=args.part_megabytes * 1024 * 1024,
            page_size=args.page_size,
        )
        started = time.monotonic()
        generated = await service.generate(chat_id=1)
        try:
            return {
                "messages": args.messages,
                "text_size": args.text_size,
                "page_size": args.page_size,
                "parts": len(generated.parts),
                "archive_bytes": sum(part.size for part in generated.parts),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "peak_rss_bytes": _peak_rss_bytes(),
            }
        finally:
            generated.cleanup()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bounded-memory message archive export benchmark")
    parser.add_argument("--messages", type=int, default=100_000)
    parser.add_argument("--text-size", type=int, default=256)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--part-megabytes", type=int, default=45)
    return parser.parse_args()


def main() -> None:
    result = asyncio.run(_run(_arguments()))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

