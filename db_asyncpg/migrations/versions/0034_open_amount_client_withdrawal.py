"""Allow /отпр without an expected amount.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-24
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0034_open_amount_client_withdrawal.sql")


def downgrade() -> None:
    raise NotImplementedError("Payment watch history must remain auditable")
