"""Reverse the duplicated Leviathan RUB write-off.

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-22
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0033_leviathan_duplicate_writeoff_reversal.sql")


def downgrade() -> None:
    raise NotImplementedError("Financial corrections are append-only")
