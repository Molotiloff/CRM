"""Link USDT fulfillment execution to watch, event and position movement.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-25
"""

from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0013_fulfillment_execution.sql")


def downgrade() -> None:
    raise NotImplementedError("Fulfillment execution links are audit data")
