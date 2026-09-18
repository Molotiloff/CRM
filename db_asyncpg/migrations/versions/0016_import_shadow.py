"""Add resumable imports and persisted dashboard shadow reports.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-26
"""

from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0016_import_shadow.sql")


def downgrade() -> None:
    raise NotImplementedError("Import and comparison reports are audit data")
