"""Claim each blockchain event for exactly one settlement.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-25
"""

from __future__ import annotations

from db_asyncpg.migrations.loader import execute_sql_file

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0012_settlement_event_claim.sql")


def downgrade() -> None:
    raise NotImplementedError("Blockchain event claims are audit invariants")
