"""Add linked and atomic office cash settlements.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-25
"""

from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0014_cash_settlements.sql")


def downgrade() -> None:
    raise NotImplementedError("Cash settlement journal is audit data")
