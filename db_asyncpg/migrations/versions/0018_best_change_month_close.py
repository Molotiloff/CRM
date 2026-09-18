"""Add auditable monthly BestChange closures.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-01
"""

from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0018_best_change_month_close.sql")


def downgrade() -> None:
    raise NotImplementedError("BestChange month closures are audit data")
