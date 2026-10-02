"""Remember the client notification to edit on subsequent deal status changes."""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0038_deal_status_messages.sql")


def downgrade() -> None:
    raise NotImplementedError("Deal status message references must remain auditable")
