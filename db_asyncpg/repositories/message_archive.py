from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo
from services.message_archive.models import (
    ArchiveMessage,
    SavedAttachment,
    SaveMessageResult,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class MessageArchiveRepo(ConnectionBoundRepo):
    async def _upsert_chat(self, con, message: ArchiveMessage) -> int:
        chat = message.chat
        row = None
        inserted = False
        if chat.telegram_chat_id is not None:
            row = await con.fetchrow(
                "SELECT id, current_title FROM message_archive_chats WHERE telegram_chat_id = $1 FOR UPDATE",
                chat.telegram_chat_id,
            )
        if row is None and chat.desktop_chat_id is not None:
            row = await con.fetchrow(
                "SELECT id, current_title FROM message_archive_chats WHERE desktop_chat_id = $1 FOR UPDATE",
                chat.desktop_chat_id,
            )

        if row is None:
            row = await con.fetchrow(
                """
                INSERT INTO message_archive_chats (
                    telegram_chat_id, desktop_chat_id, chat_type, current_title,
                    username, first_message_at, last_message_at, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $6, $7::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING id, current_title
                """,
                chat.telegram_chat_id,
                chat.desktop_chat_id,
                chat.chat_type,
                chat.title,
                chat.username,
                message.sent_at,
                _json(chat.metadata),
            )
            inserted = row is not None
            if row is None and chat.telegram_chat_id is not None:
                row = await con.fetchrow(
                    "SELECT id, current_title FROM message_archive_chats "
                    "WHERE telegram_chat_id = $1 FOR UPDATE",
                    chat.telegram_chat_id,
                )
            if row is None and chat.desktop_chat_id is not None:
                row = await con.fetchrow(
                    "SELECT id, current_title FROM message_archive_chats "
                    "WHERE desktop_chat_id = $1 FOR UPDATE",
                    chat.desktop_chat_id,
                )
            if row is None:
                raise RuntimeError("Archive chat upsert did not return a row")
        if not inserted:
            await con.execute(
                """
                UPDATE message_archive_chats
                SET telegram_chat_id = COALESCE(telegram_chat_id, $2),
                    desktop_chat_id = COALESCE(desktop_chat_id, $3),
                    chat_type = $4,
                    current_title = $5,
                    username = COALESCE($6, username),
                    first_message_at = LEAST(COALESCE(first_message_at, $7), $7),
                    last_message_at = GREATEST(COALESCE(last_message_at, $7), $7),
                    metadata = metadata || $8::jsonb,
                    updated_at = NOW()
                WHERE id = $1
                """,
                int(row["id"]),
                chat.telegram_chat_id,
                chat.desktop_chat_id,
                chat.chat_type,
                chat.title,
                chat.username,
                message.sent_at,
                _json(chat.metadata),
            )

        chat_id = int(row["id"])
        await con.execute(
            """
            INSERT INTO message_archive_chat_names (chat_id, name, normalized_name, valid_from)
            VALUES ($1, $2, LOWER(TRIM($2)), $3)
            ON CONFLICT (chat_id, normalized_name) DO NOTHING
            """,
            chat_id,
            chat.title,
            message.sent_at,
        )
        return chat_id

    async def _upsert_author(self, con, message: ArchiveMessage) -> int | None:
        author = message.author
        if author is None:
            return None
        row = None
        inserted = False
        if author.telegram_user_id is not None:
            row = await con.fetchrow(
                "SELECT id FROM message_archive_authors WHERE telegram_user_id = $1 FOR UPDATE",
                author.telegram_user_id,
            )
        if row is None and author.desktop_source_key:
            row = await con.fetchrow(
                "SELECT id FROM message_archive_authors WHERE desktop_source_key = $1 FOR UPDATE",
                author.desktop_source_key,
            )
        if row is None:
            row = await con.fetchrow(
                """
                INSERT INTO message_archive_authors (
                    telegram_user_id, desktop_source_key, display_name, username, is_bot, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                author.telegram_user_id,
                author.desktop_source_key,
                author.display_name,
                author.username,
                author.is_bot,
                _json(author.metadata),
            )
            inserted = row is not None
            if row is None and author.telegram_user_id is not None:
                row = await con.fetchrow(
                    "SELECT id FROM message_archive_authors "
                    "WHERE telegram_user_id = $1 FOR UPDATE",
                    author.telegram_user_id,
                )
            if row is None and author.desktop_source_key:
                row = await con.fetchrow(
                    "SELECT id FROM message_archive_authors "
                    "WHERE desktop_source_key = $1 FOR UPDATE",
                    author.desktop_source_key,
                )
            if row is None:
                raise RuntimeError("Archive author upsert did not return a row")
        if not inserted:
            await con.execute(
                """
                UPDATE message_archive_authors
                SET telegram_user_id = COALESCE(telegram_user_id, $2),
                    desktop_source_key = COALESCE(desktop_source_key, $3),
                    display_name = $4,
                    username = COALESCE($5, username),
                    is_bot = $6,
                    metadata = metadata || $7::jsonb,
                    updated_at = NOW()
                WHERE id = $1
                """,
                int(row["id"]),
                author.telegram_user_id,
                author.desktop_source_key,
                author.display_name,
                author.username,
                author.is_bot,
                _json(author.metadata),
            )
        return int(row["id"])

    async def save_archive_message(self, message: ArchiveMessage) -> SaveMessageResult:
        async with self._connection() as con, con.transaction():
            chat_id = await self._upsert_chat(con, message)
            author_id = await self._upsert_author(con, message)
            existing = await con.fetchrow(
                """
                SELECT id, source, content_hash, revision_no
                FROM message_archive_messages
                WHERE chat_id = $1 AND telegram_message_id = $2
                FOR UPDATE
                """,
                chat_id,
                message.telegram_message_id,
            )

            action = "skipped"
            if existing is None:
                row = await con.fetchrow(
                    """
                    INSERT INTO message_archive_messages (
                        chat_id, telegram_message_id, author_id, message_type, direction,
                        sent_at, edited_at, reply_to_message_id, media_group_id,
                        text_plain, text_entities, forward_info, raw_payload,
                        source, revision_no, content_hash
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9,
                        $10, $11::jsonb, $12::jsonb, $13::jsonb, $14, 1, $15
                    ) RETURNING id
                    """,
                    chat_id,
                    message.telegram_message_id,
                    author_id,
                    message.message_type,
                    message.direction,
                    message.sent_at,
                    message.edited_at,
                    message.reply_to_message_id,
                    message.media_group_id,
                    message.text_plain,
                    _json(message.text_entities),
                    _json(message.forward_info) if message.forward_info else None,
                    _json(message.raw_payload),
                    message.source,
                    message.content_hash,
                )
                message_id = int(row["id"])
                revision_no = 1
                action = "created"
            else:
                message_id = int(existing["id"])
                revision_no = int(existing["revision_no"])
                may_replace = not (
                    existing["source"] == "bot_api" and message.source == "telegram_desktop"
                )
                if may_replace and existing["content_hash"] != message.content_hash:
                    revision_no += 1
                    await con.execute(
                        """
                        UPDATE message_archive_messages
                        SET author_id = COALESCE($2, author_id), message_type = $3,
                            direction = $4, sent_at = $5,
                            edited_at = COALESCE($6, edited_at), reply_to_message_id = $7,
                            media_group_id = $8, text_plain = $9,
                            text_entities = $10::jsonb, forward_info = $11::jsonb,
                            raw_payload = $12::jsonb, source = $13,
                            revision_no = $14, content_hash = $15, updated_at = NOW()
                        WHERE id = $1
                        """,
                        message_id,
                        author_id,
                        message.message_type,
                        message.direction,
                        message.sent_at,
                        message.edited_at,
                        message.reply_to_message_id,
                        message.media_group_id,
                        message.text_plain,
                        _json(message.text_entities),
                        _json(message.forward_info) if message.forward_info else None,
                        _json(message.raw_payload),
                        message.source,
                        revision_no,
                        message.content_hash,
                    )
                    action = "updated"

            if action in {"created", "updated"}:
                await con.execute(
                    """
                    INSERT INTO message_archive_revisions (
                        message_id, revision_no, text_plain, text_entities,
                        edited_at, raw_payload, content_hash
                    ) VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb, $7)
                    ON CONFLICT (message_id, content_hash) DO NOTHING
                    """,
                    message_id,
                    revision_no,
                    message.text_plain,
                    _json(message.text_entities),
                    message.edited_at or message.sent_at,
                    _json(message.raw_payload),
                    message.content_hash,
                )

            saved_attachments: list[SavedAttachment] = []
            for attachment in message.attachments:
                row = await con.fetchrow(
                    """
                    INSERT INTO message_archive_attachments (
                        message_id, attachment_type, ordinal, telegram_file_id,
                        telegram_file_unique_id, mime_type, original_name, file_size,
                        duration_seconds, width, height, download_status, metadata
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb
                    )
                    ON CONFLICT (message_id, attachment_type, ordinal) DO UPDATE SET
                        telegram_file_id = COALESCE(EXCLUDED.telegram_file_id, message_archive_attachments.telegram_file_id),
                        telegram_file_unique_id = COALESCE(EXCLUDED.telegram_file_unique_id, message_archive_attachments.telegram_file_unique_id),
                        mime_type = COALESCE(EXCLUDED.mime_type, message_archive_attachments.mime_type),
                        original_name = COALESCE(EXCLUDED.original_name, message_archive_attachments.original_name),
                        file_size = COALESCE(EXCLUDED.file_size, message_archive_attachments.file_size),
                        duration_seconds = COALESCE(EXCLUDED.duration_seconds, message_archive_attachments.duration_seconds),
                        width = COALESCE(EXCLUDED.width, message_archive_attachments.width),
                        height = COALESCE(EXCLUDED.height, message_archive_attachments.height),
                        metadata = message_archive_attachments.metadata || EXCLUDED.metadata,
                        download_status = CASE
                            WHEN message_archive_attachments.download_status = 'ready' THEN 'ready'
                            ELSE EXCLUDED.download_status
                        END,
                        updated_at = NOW()
                    RETURNING id, attachment_type, ordinal, telegram_file_id,
                              download_status, metadata->>'source_path' AS source_path
                    """,
                    message_id,
                    attachment.attachment_type,
                    attachment.ordinal,
                    attachment.telegram_file_id,
                    attachment.telegram_file_unique_id,
                    attachment.mime_type,
                    attachment.original_name,
                    attachment.file_size,
                    attachment.duration_seconds,
                    attachment.width,
                    attachment.height,
                    attachment.download_status,
                    _json({**attachment.metadata, "source_path": attachment.source_path}),
                )
                saved_attachments.append(
                    SavedAttachment(
                        id=int(row["id"]),
                        attachment_type=str(row["attachment_type"]),
                        ordinal=int(row["ordinal"]),
                        telegram_file_id=row["telegram_file_id"],
                        source_path=row["source_path"],
                        download_status=str(row["download_status"]),
                    )
                )

            return SaveMessageResult(
                message_id=message_id,
                chat_id=chat_id,
                sent_at=message.sent_at,
                action=action,  # type: ignore[arg-type]
                attachments=tuple(saved_attachments),
            )

    async def mark_archive_attachment_ready(
        self, *, attachment_id: int, storage_key: str, sha256: str, file_size: int
    ) -> None:
        async with self._connection() as con:
            await con.execute(
                """
                UPDATE message_archive_attachments
                SET storage_key = $2, sha256 = $3, file_size = $4,
                    download_status = 'ready', last_error = NULL, updated_at = NOW()
                WHERE id = $1
                """,
                attachment_id,
                storage_key,
                sha256,
                file_size,
            )

    async def mark_archive_attachment_failed(
        self, *, attachment_id: int, error: str, unavailable: bool = False
    ) -> None:
        async with self._connection() as con:
            await con.execute(
                """
                UPDATE message_archive_attachments
                SET download_status = $2, download_attempts = download_attempts + 1,
                    last_error = LEFT($3, 1000), updated_at = NOW()
                WHERE id = $1
                """,
                attachment_id,
                "unavailable" if unavailable else "failed",
                error,
            )

    async def list_pending_archive_attachments(self, *, limit: int = 500) -> list[dict[str, Any]]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT a.*, m.chat_id, m.telegram_message_id, m.sent_at
                FROM message_archive_attachments a
                JOIN message_archive_messages m ON m.id = a.message_id
                WHERE a.download_status IN ('pending', 'failed')
                  AND a.attachment_type IN ('photo', 'voice')
                  AND a.download_attempts < 5
                ORDER BY a.id
                LIMIT $1
                """,
                limit,
            )
            return [dict(row) for row in rows]

    async def search_archive_chats(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        normalized = query.strip().lower()
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT DISTINCT c.*,
                    CASE
                        WHEN LOWER(TRIM(c.current_title)) = $1 THEN 0
                        WHEN LOWER(TRIM(client.name)) = $1 THEN 1
                        ELSE 2
                    END AS rank
                FROM message_archive_chats c
                LEFT JOIN message_archive_chat_names n ON n.chat_id = c.id
                LEFT JOIN clients client
                    ON client.chat_id = c.telegram_chat_id
                   AND client.is_active = TRUE
                WHERE c.is_enabled
                  AND (LOWER(TRIM(c.current_title)) LIKE '%' || $1 || '%'
                       OR n.normalized_name LIKE '%' || $1 || '%'
                       OR LOWER(TRIM(client.name)) LIKE '%' || $1 || '%')
                ORDER BY rank, c.current_title, c.id
                LIMIT $2
                """,
                normalized,
                limit,
            )
            return [dict(row) for row in rows]

    async def get_archive_chat(self, chat_id: int) -> dict[str, Any] | None:
        async with self._connection() as con:
            row = await con.fetchrow("SELECT * FROM message_archive_chats WHERE id = $1", chat_id)
            return dict(row) if row else None

    async def list_archive_messages_page(
        self,
        *,
        chat_id: int,
        cursor_sent_at: datetime | None = None,
        cursor_message_id: int | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT m.*, a.display_name AS author_name, a.username AS author_username,
                       a.is_bot AS author_is_bot
                FROM message_archive_messages m
                LEFT JOIN message_archive_authors a ON a.id = m.author_id
                WHERE m.chat_id = $1
                  AND ($2::timestamptz IS NULL OR (m.sent_at, m.telegram_message_id) > ($2, $3))
                ORDER BY m.sent_at, m.telegram_message_id
                LIMIT $4
                """,
                chat_id,
                cursor_sent_at,
                cursor_message_id,
                limit,
            )
            return [dict(row) for row in rows]

    async def list_archive_attachments_for_messages(
        self, message_ids: list[int]
    ) -> list[dict[str, Any]]:
        if not message_ids:
            return []
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT * FROM message_archive_attachments
                WHERE message_id = ANY($1::bigint[])
                ORDER BY message_id, ordinal, id
                """,
                message_ids,
            )
            return [dict(row) for row in rows]

    async def get_archive_statistics(self) -> dict[str, int]:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM message_archive_chats) AS chats,
                    (SELECT COUNT(*) FROM message_archive_messages) AS messages,
                    (SELECT COUNT(*) FROM message_archive_revisions) AS revisions,
                    (SELECT COUNT(*) FROM message_archive_attachments) AS attachments,
                    (SELECT COUNT(*) FROM message_archive_attachments
                     WHERE download_status = 'pending') AS media_pending,
                    (SELECT COUNT(*) FROM message_archive_attachments
                     WHERE download_status = 'failed') AS media_failed,
                    (SELECT COUNT(*) FROM message_archive_attachments
                     WHERE download_status = 'unavailable') AS media_unavailable,
                    (SELECT COUNT(*) FROM message_archive_imports
                     WHERE status = 'running') AS imports_running,
                    (SELECT COUNT(*) FROM message_archive_exports
                     WHERE status = 'running') AS exports_running
                """
            )
            return {key: int(value) for key, value in dict(row).items()}

    async def create_archive_import(
        self,
        *,
        source_path: str,
        source_checksum: str,
        dry_run: bool,
        summary: dict[str, Any] | None = None,
    ) -> int:
        async with self._connection() as con:
            return int(
                await con.fetchval(
                    """
                    INSERT INTO message_archive_imports (
                        source_path, source_checksum, dry_run, status, summary
                    ) VALUES ($1, $2, $3, 'running', $4::jsonb)
                    RETURNING id
                    """,
                    source_path,
                    source_checksum,
                    dry_run,
                    _json(summary or {}),
                )
            )

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
    ) -> None:
        async with self._connection() as con:
            await con.execute(
                """
                UPDATE message_archive_imports
                SET status = $2, chats_found = $3, messages_created = $4,
                    messages_updated = $5, messages_skipped = $6,
                    files_missing = $7, summary = summary || $8::jsonb,
                    error = LEFT($9, 2000), finished_at = NOW()
                WHERE id = $1
                """,
                import_id,
                status,
                chats_found,
                messages_created,
                messages_updated,
                messages_skipped,
                files_missing,
                _json(summary or {}),
                error,
            )

    async def create_archive_export(
        self, *, chat_id: int, requested_by_user_id: int, requested_in_chat_id: int
    ) -> int:
        async with self._connection() as con:
            return int(
                await con.fetchval(
                    """
                    INSERT INTO message_archive_exports (
                        chat_id, requested_by_user_id, requested_in_chat_id, status
                    ) VALUES ($1, $2, $3, 'running') RETURNING id
                    """,
                    chat_id,
                    requested_by_user_id,
                    requested_in_chat_id,
                )
            )

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
    ) -> None:
        async with self._connection() as con:
            await con.execute(
                """
                UPDATE message_archive_exports
                SET status = $2, message_count = $3, part_count = $4,
                    total_size = $5, telegram_result_message_ids = $6::jsonb,
                    error = LEFT($7, 2000), finished_at = NOW()
                WHERE id = $1
                """,
                export_id,
                status,
                message_count,
                part_count,
                total_size,
                _json(telegram_message_ids or []),
                error,
            )
