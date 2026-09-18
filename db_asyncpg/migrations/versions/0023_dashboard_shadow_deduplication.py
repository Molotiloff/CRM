"""Deduplicate dashboard shadow reports by comparison content.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-06
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0023_dashboard_shadow_deduplication.sql")


def downgrade() -> None:
    raise NotImplementedError("Dashboard comparison reports are audit data")
