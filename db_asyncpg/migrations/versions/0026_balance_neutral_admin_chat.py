"""Exclude the balance-neutral admin request chat from client liabilities.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-18
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0026_balance_neutral_admin_chat.sql")


def downgrade() -> None:
    raise NotImplementedError("Admin chat classification is accounting policy")
