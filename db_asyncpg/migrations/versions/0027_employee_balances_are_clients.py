"""Remove imported employee-balance duplicates from internal accounts.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-18
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0027_employee_balances_are_clients.sql")


def downgrade() -> None:
    raise NotImplementedError("Employee balances are authoritative client ledgers")
