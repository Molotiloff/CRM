"""CRM: связь статуса сделки с ожиданием оплаты.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-02
"""
from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0007_deal_status_workflow.sql")


def downgrade() -> None:
    raise NotImplementedError("Откат статусной модели сделок не поддерживается")
