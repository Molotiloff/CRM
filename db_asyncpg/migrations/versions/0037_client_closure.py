"""Keep explicitly zeroed client wallets closed."""

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0037_client_closure.sql")


def downgrade() -> None:
    raise NotImplementedError("Client closure history must remain auditable")
