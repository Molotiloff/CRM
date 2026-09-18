"""Import forgotten legacy clients Django and 013.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-18
"""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0029_legacy_clients_django_013.sql")


def downgrade() -> None:
    raise NotImplementedError("Legacy client opening balances are append-only")
