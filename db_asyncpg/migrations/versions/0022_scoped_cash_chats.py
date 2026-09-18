"""Allow a cash chat to own only selected wallet currencies.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-03
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0022_scoped_cash_chats.sql")


def downgrade() -> None:
    raise NotImplementedError("Cash chat accounting scopes are audit configuration")
