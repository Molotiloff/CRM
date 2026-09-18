from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from db_asyncpg.repositories.message_archive import MessageArchiveRepo
from services.message_archive.models import (
    ArchiveAttachment,
    ArchiveAuthor,
    ArchiveChat,
    ArchiveMessage,
)


def _message(*, text: str, content_hash: str) -> ArchiveMessage:
    return ArchiveMessage(
        chat=ArchiveChat(
            title="Архивный клиент",
            chat_type="supergroup",
            telegram_chat_id=-100500,
        ),
        telegram_message_id=42,
        author=ArchiveAuthor(
            display_name="Менеджер",
            telegram_user_id=100,
        ),
        message_type="photo",
        direction="inbound",
        sent_at=datetime(2026, 9, 19, 12, tzinfo=UTC),
        text_plain=text,
        source="bot_api",
        content_hash=content_hash,
        attachments=(
            ArchiveAttachment(
                attachment_type="photo",
                telegram_file_id="file-id",
                telegram_file_unique_id="unique-id",
                download_status="pending",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_message_archive_repository_is_idempotent_and_preserves_revisions(pool) -> None:
    repository = MessageArchiveRepo(pool)
    original = _message(text="Первая версия", content_hash="hash-v1")

    created = await repository.save_archive_message(original)
    skipped = await repository.save_archive_message(original)
    updated = await repository.save_archive_message(
        replace(original, text_plain="Исправлено", content_hash="hash-v2")
    )

    assert created.action == "created"
    assert skipped.action == "skipped"
    assert updated.action == "updated"
    assert created.message_id == skipped.message_id == updated.message_id

    async with pool.acquire() as connection:
        message = await connection.fetchrow(
            """
            SELECT text_plain, revision_no
            FROM message_archive_messages
            WHERE id = $1
            """,
            created.message_id,
        )
        revisions = await connection.fetchval(
            "SELECT COUNT(*) FROM message_archive_revisions WHERE message_id = $1",
            created.message_id,
        )
        attachments = await connection.fetchval(
            "SELECT COUNT(*) FROM message_archive_attachments WHERE message_id = $1",
            created.message_id,
        )

    assert dict(message) == {"text_plain": "Исправлено", "revision_no": 2}
    assert revisions == 2
    assert attachments == 1


@pytest.mark.asyncio
async def test_message_archive_search_uses_current_client_name(pool) -> None:
    repository = MessageArchiveRepo(pool)
    saved = await repository.save_archive_message(
        _message(text="Тест", content_hash="hash-search")
    )
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO clients(chat_id, name) VALUES ($1, $2)",
            -100500,
            "Новое имя клиента",
        )

    matches = await repository.search_archive_chats("новое имя")

    assert [row["id"] for row in matches] == [saved.chat_id]
