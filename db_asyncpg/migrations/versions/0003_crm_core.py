"""CRM: все новые таблицы раздела 3.2 workflow_crm.md (этап C0.4).

users, counterparties, deals (+deal_legs, +deal_status_events), client_kt_fees,
cash_desks(+moves), expenses, capital_owners/moves/payouts, firm_position_moves,
firm_wallet_facts, internal_accounts(+moves), attendance, client_comments,
tg_outbox, ref_values + view crm_sales/crm_purchases.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-08
"""
from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0003_crm_core.sql")


def downgrade() -> None:
    raise NotImplementedError("Откат CRM-схемы не поддерживается")
