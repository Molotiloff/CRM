"""Add auditable BestChange deal corrections.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-01
"""

from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0020_best_change_corrections.sql")


def downgrade() -> None:
    raise NotImplementedError("BestChange corrections are audit data")
