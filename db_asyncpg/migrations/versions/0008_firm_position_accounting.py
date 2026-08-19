"""Strengthen the append-only firm position accounting journal.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-19
"""
from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0008_firm_position_accounting.sql")


def downgrade() -> None:
    raise NotImplementedError("Firm position accounting migration is append-only")
