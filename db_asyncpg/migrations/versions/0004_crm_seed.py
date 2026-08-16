"""CRM: seed справочников из листа «Данные» (workflow_crm.md 4.6).

ref_values (города, валюты, категории расходов, курьеры, виды перестановок,
отметки посещаемости), capital_owners со ставками, counterparties,
users из managers. Идемпотентно (ON CONFLICT DO NOTHING).

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-08
"""
from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0004_crm_seed.sql")


def downgrade() -> None:
    raise NotImplementedError("Откат seed-данных не поддерживается")
