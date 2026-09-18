"""Apply fixed cutover corrections and deactivate duplicated legacy accounts.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-18
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0030_legacy_balance_cleanup.sql")


def downgrade() -> None:
    raise NotImplementedError("Financial corrections are append-only")
