"""Baseline: схема бота до CRM (15 таблиц + request_id_seq).

Снимок db_asyncpg/schema.sql на момент внедрения Alembic (workflow.md 2.1,
сверен с боевой БД через pg_dump). SQL идемпотентен (IF NOT EXISTS), поэтому
ревизию можно безопасно прогонять и по уже существующей БД — stamp не нужен.

Revision ID: 0001
Revises:
Create Date: 2026-07-08
"""
from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0001_baseline.sql")


def downgrade() -> None:
    raise NotImplementedError("Откат baseline не поддерживается")
