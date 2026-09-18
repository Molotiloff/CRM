"""Add auditable BestChange partner and CoinDrop payments.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-01
"""

from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0019_best_change_payments.sql")


def downgrade() -> None:
    raise NotImplementedError("BestChange payments are audit data")
