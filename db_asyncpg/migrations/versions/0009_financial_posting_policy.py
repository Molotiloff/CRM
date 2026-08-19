"""Use the canonical financially-posted deal predicate in reporting views.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-19
"""

from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0009_financial_posting_policy.sql")


def downgrade() -> None:
    raise NotImplementedError("The financial posting policy cannot be weakened")
