"""Append-only corrections of client transfers.

Revision ID: 0036
Revises: 0035
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0036_client_transfer_adjustments.sql")


def downgrade() -> None:
    raise NotImplementedError("Client transfer correction history must remain auditable")
