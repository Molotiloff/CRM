from __future__ import annotations

from decimal import Decimal

import asyncpg

from domain import (
    CurrencyCode,
    DomainStateError,
    FirmPosition,
    FirmPositionMove,
    FirmPositionMoveKind,
    NewFirmPositionMove,
    firm_position_currency,
)

from .base import ConnectionBoundRepo

_MOVE_COLUMNS = """
    id, currency_code, deal_id, deal_leg_id, kind, qty, rub_amount,
    qty_after, rub_cost_after, entry_rate, effective_at, created_at,
    created_by, idempotency_key, reversal_of_id, reason
"""


class FirmPositionsRepo(ConnectionBoundRepo):
    """Asyncpg adapter for the append-only firm position journal."""

    async def acquire_currency_lock(self, currency: CurrencyCode) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('firm_position:' || $1, 0))",
                str(currency),
            )

    async def get_current(self, currency: CurrencyCode) -> FirmPosition:
        normalized = firm_position_currency(currency)
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                SELECT qty_after, rub_cost_after
                FROM firm_position_moves
                WHERE currency_code = $1
                ORDER BY id DESC
                LIMIT 1
                """,
                str(normalized),
            )
        if row is None:
            return FirmPosition(normalized, Decimal(0), Decimal(0))
        return FirmPosition(
            normalized,
            Decimal(str(row["qty_after"])),
            Decimal(str(row["rub_cost_after"])),
        )

    async def list_current(self) -> list[FirmPosition]:
        async with self._connection() as connection:
            rows = await connection.fetch(
                """
                SELECT DISTINCT ON (currency_code)
                       currency_code, qty_after, rub_cost_after
                FROM firm_position_moves
                ORDER BY currency_code, id DESC
                """
            )
        return [
            FirmPosition(
                firm_position_currency(str(row["currency_code"])),
                Decimal(str(row["qty_after"])),
                Decimal(str(row["rub_cost_after"])),
            )
            for row in rows
        ]

    async def append(self, move: NewFirmPositionMove) -> FirmPositionMove:
        async with self._connection() as connection:
            try:
                row = await connection.fetchrow(
                    f"""
                    INSERT INTO firm_position_moves(
                        currency_code, deal_id, deal_leg_id, kind, qty, rub_amount,
                        qty_after, rub_cost_after, entry_rate, effective_at,
                        created_by, idempotency_key, reversal_of_id, reason
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
                    )
                    RETURNING {_MOVE_COLUMNS}
                    """,
                    str(move.currency),
                    move.deal_id,
                    move.deal_leg_id,
                    move.kind.value,
                    move.qty,
                    move.rub_amount,
                    move.qty_after,
                    move.rub_cost_after,
                    move.entry_rate,
                    move.effective_at,
                    move.created_by,
                    move.idempotency_key,
                    move.reversal_of_id,
                    move.reason,
                )
            except asyncpg.UniqueViolationError as exc:
                constraint = exc.constraint_name or ""
                messages = {
                    "uq_firm_position_moves_idempotency": (
                        "Position idempotency key has already been applied"
                    ),
                    "uq_firm_position_moves_reversal": (
                        "Position move has already been reversed"
                    ),
                    "uq_firm_position_moves_deal_leg": (
                        "Deal leg has already been applied to firm position"
                    ),
                }
                if constraint in messages:
                    raise DomainStateError(messages[constraint]) from exc
                raise
        if row is None:
            raise RuntimeError("Firm position move insert returned no row")
        return self._move_from_row(row)

    async def get_by_idempotency_key(self, key: str) -> FirmPositionMove | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_MOVE_COLUMNS} FROM firm_position_moves WHERE idempotency_key = $1",
                key,
            )
        return self._move_from_row(row) if row is not None else None

    async def get_move(self, move_id: int) -> FirmPositionMove | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_MOVE_COLUMNS} FROM firm_position_moves WHERE id = $1",
                move_id,
            )
        return self._move_from_row(row) if row is not None else None

    async def list_positions(self) -> list[dict]:
        """Compatibility read for the existing statistics service."""
        positions = await self.list_current()
        return [
            {
                "currency_code": str(position.currency),
                "qty_after": position.qty,
                "rub_cost_after": position.rub_cost,
            }
            for position in positions
        ]

    async def set_wallet_fact(
        self,
        *,
        currency_code: str,
        actual_qty: Decimal,
        comment: str | None = None,
        updated_by: int | None = None,
    ) -> None:
        """Legacy fact writer retained until wallet snapshots are introduced."""
        currency = firm_position_currency(currency_code)
        async with self._connection() as connection:
            await connection.execute(
                """
                INSERT INTO firm_wallet_facts(
                    currency_code, actual_qty, comment, updated_by, updated_at
                )
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (currency_code) DO UPDATE
                SET actual_qty = EXCLUDED.actual_qty,
                    comment = EXCLUDED.comment,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                """,
                str(currency),
                actual_qty,
                comment,
                updated_by,
            )

    async def get_wallet_facts(self) -> dict[str, Decimal]:
        async with self._connection() as connection:
            rows = await connection.fetch(
                "SELECT currency_code, actual_qty FROM firm_wallet_facts"
            )
        return {
            str(row["currency_code"]): Decimal(str(row["actual_qty"])) for row in rows
        }

    @staticmethod
    def _move_from_row(row) -> FirmPositionMove:
        entry_rate = row["entry_rate"]
        return FirmPositionMove(
            id=int(row["id"]),
            currency=firm_position_currency(str(row["currency_code"])),
            kind=FirmPositionMoveKind(str(row["kind"])),
            qty=Decimal(str(row["qty"])),
            rub_amount=Decimal(str(row["rub_amount"])),
            qty_after=Decimal(str(row["qty_after"])),
            rub_cost_after=Decimal(str(row["rub_cost_after"])),
            idempotency_key=str(row["idempotency_key"]),
            effective_at=row["effective_at"],
            created_at=row["created_at"],
            deal_id=int(row["deal_id"]) if row["deal_id"] is not None else None,
            deal_leg_id=(
                int(row["deal_leg_id"]) if row["deal_leg_id"] is not None else None
            ),
            entry_rate=Decimal(str(entry_rate)) if entry_rate is not None else None,
            created_by=(
                int(row["created_by"]) if row["created_by"] is not None else None
            ),
            reason=str(row["reason"]) if row["reason"] is not None else None,
            reversal_of_id=(
                int(row["reversal_of_id"])
                if row["reversal_of_id"] is not None
                else None
            ),
        )
