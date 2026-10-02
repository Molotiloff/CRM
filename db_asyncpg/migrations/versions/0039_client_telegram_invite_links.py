"""Store optional Telegram invite links for private client chats."""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0039_client_telegram_invite_links.sql")


def downgrade() -> None:
    raise NotImplementedError("Client Telegram invite links should remain available")
