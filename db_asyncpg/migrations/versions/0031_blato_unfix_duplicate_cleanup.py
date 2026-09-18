"""Deactivate the duplicated Blato unfix internal balance.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-18
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0031_blato_unfix_duplicate_cleanup.sql")


def downgrade() -> None:
    raise NotImplementedError("Financial corrections are append-only")
