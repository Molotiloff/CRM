"""Backfill legacy wallet facts into the append-only snapshot journal.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-25
"""

from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0011_wallet_fact_backfill.sql")


def downgrade() -> None:
    raise NotImplementedError("Wallet fact snapshots are audit data and cannot be removed")
