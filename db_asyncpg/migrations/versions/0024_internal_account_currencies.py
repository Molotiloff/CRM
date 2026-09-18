"""Allow internal reconciliation accounts in canonical currencies.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-08
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0024_internal_account_currencies.sql")


def downgrade() -> None:
    raise NotImplementedError("Internal account currencies are accounting data")

