from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from services.message_archive.html_renderer import MessageHtmlRenderer
from services.message_archive.media_storage import MediaStoragePort
from services.message_archive.models import ExportPart
from services.message_archive.ports import MessageArchiveRepositoryPort


@dataclass(slots=True)
class GeneratedExport:
    workdir: Path
    parts: list[ExportPart]

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


class MessageExportService:
    def __init__(
        self,
        *,
        repo: MessageArchiveRepositoryPort,
        storage: MediaStoragePort,
        temp_dir: str | Path,
        part_size: int,
        page_size: int = 500,
        renderer: MessageHtmlRenderer | None = None,
    ) -> None:
        self.repo = repo
        self.storage = storage
        self.temp_dir = Path(temp_dir).expanduser().resolve()
        self.temp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.temp_dir.chmod(0o700)
        self.part_size = max(1024 * 1024, part_size)
        self.page_size = max(10, page_size)
        self.renderer = renderer or MessageHtmlRenderer()

    async def generate(self, *, chat_id: int) -> GeneratedExport:
        chat = await self.repo.get_archive_chat(chat_id)
        if not chat:
            raise LookupError("Архивный чат не найден")
        workdir = Path(tempfile.mkdtemp(prefix="message-export-", dir=self.temp_dir))
        parts: list[ExportPart] = []
        try:
            await self._generate_parts(chat=chat, workdir=workdir, parts=parts)
            return GeneratedExport(workdir=workdir, parts=parts)
        except Exception:
            shutil.rmtree(workdir, ignore_errors=True)
            raise

    async def _generate_parts(
        self, *, chat: dict[str, Any], workdir: Path, parts: list[ExportPart]
    ) -> None:
        state = self._open_part(workdir, str(chat["current_title"]), 1)
        cursor_date: datetime | None = None
        cursor_message_id: int | None = None
        last_day = None
        try:
            while True:
                page = await self.repo.list_archive_messages_page(
                    chat_id=int(chat["id"]),
                    cursor_sent_at=cursor_date,
                    cursor_message_id=cursor_message_id,
                    limit=self.page_size,
                )
                if not page:
                    break
                attachments = await self.repo.list_archive_attachments_for_messages(
                    [int(row["id"]) for row in page]
                )
                by_message: dict[int, list[dict[str, Any]]] = {}
                for attachment in attachments:
                    by_message.setdefault(int(attachment["message_id"]), []).append(attachment)

                for message in page:
                    message_attachments = by_message.get(int(message["id"]), [])
                    media_names, media_size = await self._media_names(
                        message, message_attachments
                    )
                    day = message["sent_at"].date()
                    rendered = self.renderer.render_message(
                        message,
                        message_attachments,
                        media_names=media_names,
                        show_day=day != last_day,
                    )
                    rendered_size = len(rendered.encode("utf-8"))
                    crosses_calendar_period = bool(
                        state.date_from and state.date_from.year != message["sent_at"].year
                    )
                    if (
                        state.message_count
                        and (
                            crosses_calendar_period
                            or state.estimated_size + media_size + rendered_size > self.part_size
                        )
                    ):
                        parts.append(await self._finish_part(state, chat))
                        state = self._open_part(
                            workdir, str(chat["current_title"]), len(parts) + 1
                        )
                        last_day = None
                        rendered = self.renderer.render_message(
                            message,
                            message_attachments,
                            media_names=media_names,
                            show_day=True,
                        )
                        rendered_size = len(rendered.encode("utf-8"))

                    state.html.write(rendered)
                    state.message_count += 1
                    state.estimated_size += rendered_size
                    state.date_from = state.date_from or message["sent_at"]
                    state.date_to = message["sent_at"]
                    for attachment in message_attachments:
                        archive_name = media_names.get(int(attachment["id"]))
                        if not archive_name:
                            continue
                        await self._write_media(
                            state=state,
                            storage_key=str(attachment["storage_key"]),
                            archive_name=archive_name,
                        )
                        state.estimated_size += int(attachment.get("file_size") or 0)
                    last_day = day

                cursor_date = page[-1]["sent_at"]
                cursor_message_id = int(page[-1]["telegram_message_id"])
                if len(page) < self.page_size:
                    break

            parts.append(await self._finish_part(state, chat))
        finally:
            self._abort_part(state)

    async def _media_names(
        self, message: dict[str, Any], attachments: list[dict[str, Any]]
    ) -> tuple[dict[int, str], int]:
        names: dict[int, str] = {}
        size = 0
        for attachment in attachments:
            storage_key = attachment.get("storage_key")
            if attachment.get("download_status") != "ready" or not storage_key:
                continue
            storage_key = str(storage_key)
            if not await self.storage.exists(key=storage_key):
                continue
            suffix = Path(storage_key).suffix.lower() or ".bin"
            name = (
                f"media/{attachment['attachment_type']}_{message['telegram_message_id']}_"
                f"{attachment['ordinal']}{suffix}"
            )
            names[int(attachment["id"])] = name
            size += int(attachment.get("file_size") or 0)
        return names, size

    async def _write_media(
        self, *, state: _PartState, storage_key: str, archive_name: str
    ) -> None:
        async with self.storage.open(key=storage_key) as source:
            target = state.archive.open(archive_name, "w")
            try:
                while chunk := await asyncio.to_thread(source.read, 64 * 1024):
                    await asyncio.to_thread(target.write, chunk)
            finally:
                await asyncio.to_thread(target.close)

    def _open_part(self, workdir: Path, title: str, number: int) -> _PartState:
        html_path = workdir / f"index-{number:03d}.html"
        zip_path = workdir / f"{self._safe_name(title)}_part-{number:03d}.zip"
        html_file = html_path.open("w", encoding="utf-8", newline="")
        html_file.write(self.renderer.document_start(title=title, part=number))
        archive = zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True)
        return _PartState(
            number=number,
            html_path=html_path,
            zip_path=zip_path,
            html=html_file,
            archive=archive,
        )

    async def _finish_part(self, state: _PartState, chat: dict[str, Any]) -> ExportPart:
        state.html.write(self.renderer.document_end())
        state.html.close()
        await asyncio.to_thread(state.archive.write, state.html_path, "index.html")
        manifest = json.dumps(
            {
                "chat": chat["current_title"],
                "telegram_chat_id": chat.get("telegram_chat_id"),
                "part": state.number,
                "message_count": state.message_count,
                "date_from": state.date_from.isoformat() if state.date_from else None,
                "date_to": state.date_to.isoformat() if state.date_to else None,
            },
            ensure_ascii=False,
            indent=2,
        )
        await asyncio.to_thread(
            state.archive.writestr,
            "manifest.json",
            manifest,
        )
        await asyncio.to_thread(state.archive.close)
        state.html_path.unlink(missing_ok=True)
        period = self._period_name(state.date_from, state.date_to)
        final_path = state.zip_path.with_name(
            f"{self._safe_name(str(chat['current_title']))}_{period}_part-{state.number:03d}.zip"
        )
        state.zip_path.replace(final_path)
        state.zip_path = final_path
        return ExportPart(
            path=str(state.zip_path),
            message_count=state.message_count,
            size=state.zip_path.stat().st_size,
            date_from=state.date_from,
            date_to=state.date_to,
        )

    @staticmethod
    def _abort_part(state: _PartState) -> None:
        if not state.html.closed:
            state.html.close()
        if state.archive.fp is not None:
            state.archive.close()

    @staticmethod
    def _safe_name(value: str) -> str:
        normalized = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("._")
        return normalized[:80] or "chat"

    @staticmethod
    def _period_name(date_from: datetime | None, date_to: datetime | None) -> str:
        if date_from is None or date_to is None:
            return "empty"
        start = date_from.strftime("%Y-%m-%d")
        end = date_to.strftime("%Y-%m-%d")
        return start if start == end else f"{start}_{end}"


@dataclass(slots=True)
class _PartState:
    number: int
    html_path: Path
    zip_path: Path
    html: TextIO
    archive: zipfile.ZipFile
    message_count: int = 0
    estimated_size: int = 0
    date_from: datetime | None = None
    date_to: datetime | None = None

