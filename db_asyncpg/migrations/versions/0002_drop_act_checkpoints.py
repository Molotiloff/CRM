"""Удаление устаревшей act_checkpoints (workflow.md 2.1: исключена из канона).

Таблица не используется кодом; в baseline её нет, но в старых БД может
оставаться — здесь добиваем.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-08
"""
from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0002_drop_act_checkpoints.sql")


def downgrade() -> None:
    raise NotImplementedError("act_checkpoints устарела, восстановление не поддерживается")
