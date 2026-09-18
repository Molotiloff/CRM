"""Add auditable BestChange deals and account movements.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-31
"""

from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0017_best_change.sql")


def downgrade() -> None:
    raise NotImplementedError("BestChange deals and account movements are audit data")
