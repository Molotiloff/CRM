"""Add auditable manual Moscow cash desks and migrate openings.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-02
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0021_manual_moscow_cash.sql")


def downgrade() -> None:
    raise NotImplementedError("Manual cash desk movements are audit data")
