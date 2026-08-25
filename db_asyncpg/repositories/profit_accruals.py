from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from domain import DomainStateError
from services.accounting.models import (
    MarketQuote,
    ProfitAccrual,
    ProfitSettlementStatus,
)

from .base import ConnectionBoundRepo

_COLUMNS = """
    id, deal_id, qty, idempotency_key, settlement_status, created_at,
    valuation_rate, capitalization_move_id
"""


class ProfitAccrualsRepo(ConnectionBoundRepo):
    async def acquire_capitalization_lock(self, business_date: date) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('profit:' || $1, 0))",
                business_date.isoformat(),
            )

    async def get_by_idempotency_key(self, key: str) -> ProfitAccrual | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_COLUMNS} FROM profit_usdt_accruals WHERE idempotency_key = $1",
                key,
            )
        return self._accrual(row) if row is not None else None

    async def append(
        self,
        *,
        deal_id: int,
        qty: Decimal,
        settlement_status: str,
        idempotency_key: str,
    ) -> ProfitAccrual:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"""
                INSERT INTO profit_usdt_accruals(
                    deal_id, qty, settlement_status, idempotency_key
                ) VALUES ($1, $2, $3, $4)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING {_COLUMNS}
                """,
                deal_id,
                qty,
                settlement_status,
                idempotency_key,
            )
            if row is None:
                row = await connection.fetchrow(
                    f"SELECT {_COLUMNS} FROM profit_usdt_accruals WHERE idempotency_key = $1",
                    idempotency_key,
                )
        return self._accrual(row)

    async def list_pending_before(self, boundary: datetime) -> list[ProfitAccrual]:
        async with self._connection() as connection:
            rows = await connection.fetch(
                f"""
                SELECT {_COLUMNS} FROM profit_usdt_accruals
                WHERE capitalization_status = 'pending' AND created_at < $1
                ORDER BY id FOR UPDATE
                """,
                boundary,
            )
        return [self._accrual(row) for row in rows]

    async def mark_capitalized(
        self,
        *,
        accrual_ids: tuple[int, ...],
        quote: MarketQuote,
        move_id: int,
        capitalized_at: datetime,
        actor_user_id: int | None,
        reason: str | None,
    ) -> None:
        async with self._connection() as connection:
            await connection.execute(
                """
                UPDATE profit_usdt_accruals
                SET valuation_rate = $2, rub_value = qty * $2,
                    quote_source = $3, quote_observed_at = $4,
                    valuation_actor_id = $5, valuation_reason = $6,
                    capitalization_status = 'capitalized', capitalized_at = $7,
                    capitalization_move_id = $8
                WHERE id = ANY($1::bigint[]) AND capitalization_status = 'pending'
                """,
                list(accrual_ids),
                quote.price,
                quote.source,
                quote.observed_at,
                actor_user_id,
                reason,
                capitalized_at,
                move_id,
            )

    async def mark_received(
        self,
        *,
        accrual_id: int,
        payment_event_id: int,
        received_at: datetime,
    ) -> ProfitAccrual:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"""
                UPDATE profit_usdt_accruals
                SET settlement_status = 'received', received_at = $2,
                    payment_event_id = $3
                WHERE id = $1
                  AND (settlement_status = 'in_transit' OR payment_event_id = $3)
                RETURNING {_COLUMNS}
                """,
                accrual_id,
                received_at,
                payment_event_id,
            )
        if row is None:
            raise DomainStateError(
                "Profit accrual is already linked to another payment event"
            )
        return self._accrual(row)

    @staticmethod
    def _accrual(row) -> ProfitAccrual:
        return ProfitAccrual(
            id=int(row["id"]),
            deal_id=int(row["deal_id"]),
            qty=Decimal(str(row["qty"])),
            idempotency_key=str(row["idempotency_key"]),
            settlement_status=ProfitSettlementStatus(str(row["settlement_status"])),
            created_at=row["created_at"],
            valuation_rate=(
                Decimal(str(row["valuation_rate"]))
                if row["valuation_rate"] is not None
                else None
            ),
            capitalization_move_id=(
                int(row["capitalization_move_id"])
                if row["capitalization_move_id"] is not None
                else None
            ),
        )
