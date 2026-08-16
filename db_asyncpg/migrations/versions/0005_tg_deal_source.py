"""CRM: идемпотентная связь сделки с заявкой Telegram.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-02
"""
from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0005_tg_deal_source.sql")


def downgrade() -> None:
    raise NotImplementedError("Откат связи Telegram-заявок не поддерживается")
