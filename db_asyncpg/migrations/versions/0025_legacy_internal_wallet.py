"""Classify the legacy ACT ledger as an internal operational wallet.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-08
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0025_legacy_internal_wallet.sql")


def downgrade() -> None:
    raise NotImplementedError("Legacy wallet classification is accounting policy")
