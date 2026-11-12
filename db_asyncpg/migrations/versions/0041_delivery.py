"""Delivery cash/client posting and courier reward audit."""
from db_asyncpg.migrations.loader import execute_sql_file

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0041_delivery.sql")


def downgrade() -> None:
    raise NotImplementedError("Delivery postings must remain auditable")
