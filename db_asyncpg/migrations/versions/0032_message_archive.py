"""Add durable Telegram message archive.

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-19
"""

from alembic import op
from sqlalchemy import text

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

_TABLES = {
    "message_archive_chats",
    "message_archive_chat_names",
    "message_archive_authors",
    "message_archive_messages",
    "message_archive_revisions",
    "message_archive_attachments",
    "message_archive_imports",
    "message_archive_exports",
}
_INDEXES = {
    "uq_message_archive_chats_telegram",
    "uq_message_archive_chats_desktop",
    "ix_message_archive_chats_title",
    "ix_message_archive_chat_names_normalized",
    "uq_message_archive_authors_telegram",
    "uq_message_archive_authors_desktop",
    "ix_message_archive_messages_chat_time",
    "ix_message_archive_messages_chat_author_time",
    "ix_message_archive_attachments_download",
    "ix_message_archive_attachments_sha256",
}


def upgrade() -> None:
    """Adopt the archive schema if the legacy SkyEX migration already created it."""
    connection = op.get_bind()
    existing_tables = set(
        connection.execute(
            text(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = current_schema()
                  AND tablename LIKE 'message_archive_%'
                """
            )
        ).scalars()
    )
    if not existing_tables:
        execute_sql_file("0032_message_archive.sql")
        return

    missing_tables = _TABLES - existing_tables
    existing_indexes = set(
        connection.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename LIKE 'message_archive_%'
                """
            )
        ).scalars()
    )
    missing_indexes = _INDEXES - existing_indexes
    inaccessible_tables = set(
        connection.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = ANY(:tables)
                  AND NOT has_table_privilege(
                      current_user,
                      quote_ident(table_schema) || '.' || quote_ident(table_name),
                      'SELECT,INSERT,UPDATE,DELETE'
                  )
                """
            ),
            {"tables": sorted(_TABLES)},
        ).scalars()
    )
    if missing_tables or missing_indexes or inaccessible_tables:
        raise RuntimeError(
            "Pre-existing message archive schema is incomplete: "
            f"missing tables={sorted(missing_tables)}, "
            f"missing indexes={sorted(missing_indexes)}, "
            f"inaccessible tables={sorted(inaccessible_tables)}"
        )


def downgrade() -> None:
    raise NotImplementedError("Message archive downgrade is intentionally unsupported")
