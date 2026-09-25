"""Add client-to-client transfer deals.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-24
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0035_client_transfer.sql")


def downgrade() -> None:
    raise NotImplementedError("Client transfer deal history must remain auditable")
