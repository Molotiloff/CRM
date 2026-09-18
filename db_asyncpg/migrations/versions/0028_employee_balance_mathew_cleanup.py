"""Finish employee-balance cleanup for the source spelling of Mathew.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-18
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0028_employee_balance_mathew_cleanup.sql")


def downgrade() -> None:
    raise NotImplementedError("Employee balances are authoritative client ledgers")
