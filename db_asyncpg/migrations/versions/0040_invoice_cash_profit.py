"""Allow signed cash-only invoice profit without position capitalization."""
from db_asyncpg.migrations.loader import execute_sql_file

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0040_invoice_cash_profit.sql")


def downgrade() -> None:
    raise NotImplementedError("Posted invoice profit must remain auditable")
