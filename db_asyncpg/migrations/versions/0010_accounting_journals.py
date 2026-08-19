"""Add accounting journals and registries required by the main dashboard workflow.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-20
"""

from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0010_accounting_journals.sql")


def downgrade() -> None:
    raise NotImplementedError("Accounting journals are audit data and cannot be dropped")
