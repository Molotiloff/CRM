"""Enable idempotent partner purchase funding.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-26
"""

from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0015_partner_profit_services.sql")


def downgrade() -> None:
    raise NotImplementedError("Partner accounting movements are audit data")
