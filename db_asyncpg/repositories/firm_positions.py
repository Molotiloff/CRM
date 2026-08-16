from __future__ import annotations

from decimal import Decimal

from db_asyncpg.repositories.base import ConnectionBoundRepo


class FirmPositionsRepo(ConnectionBoundRepo):
    """
    Append-only журнал валютной позиции фирмы (firm_position_moves).

    Позиция ведётся методом средневзвешенной себестоимости (спека 4.2
    workflow_crm.md): покупка увеличивает qty и rub_cost, продажа списывает
    по среднему курсу. Текущая позиция — последняя строка по валюте
    (qty_after / rub_cost_after), как transactions.balance_after.
    """

    @staticmethod
    def _norm_currency(currency_code: str) -> str:
        return currency_code.strip().upper()

    async def apply_move(
        self,
        *,
        currency_code: str,
        kind: str,  # 'purchase' | 'sale' | 'adjust'
        qty: Decimal,
        rub_amount: Decimal,
        deal_id: int | None = None,
    ) -> dict:
        async with self._connection() as con:
            async with con.transaction():
                return await self.apply_move_in(
                    con,
                    currency_code=currency_code,
                    kind=kind,
                    qty=qty,
                    rub_amount=rub_amount,
                    deal_id=deal_id,
                )

    async def apply_move_in(
        self,
        con,
        *,
        currency_code: str,
        kind: str,
        qty: Decimal,
        rub_amount: Decimal,
        deal_id: int | None = None,
    ) -> dict:
        """
        Версия для вызова внутри внешней транзакции (например, вместе с
        созданием deal). Сериализация по валюте — advisory xact lock.
        """
        cur = self._norm_currency(currency_code)
        await con.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('firm_position:' || $1, 0))",
            cur,
        )
        prev = await con.fetchrow(
            """
            SELECT qty_after, rub_cost_after
            FROM firm_position_moves
            WHERE currency_code = $1
            ORDER BY id DESC
            LIMIT 1
            """,
            cur,
        )
        prev_qty = Decimal(str(prev["qty_after"])) if prev else Decimal(0)
        prev_cost = Decimal(str(prev["rub_cost_after"])) if prev else Decimal(0)
        row = await con.fetchrow(
            """
            INSERT INTO firm_position_moves(
                currency_code, deal_id, kind, qty, rub_amount, qty_after, rub_cost_after
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, currency_code, kind, qty, rub_amount, qty_after, rub_cost_after, created_at
            """,
            cur,
            deal_id,
            kind,
            qty,
            rub_amount,
            prev_qty + qty,
            prev_cost + rub_amount,
        )
        return dict(row)

    async def get_position(self, currency_code: str) -> dict | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT currency_code, qty_after, rub_cost_after, created_at
                FROM firm_position_moves
                WHERE currency_code = $1
                ORDER BY id DESC
                LIMIT 1
                """,
                self._norm_currency(currency_code),
            )
            return dict(row) if row else None

    async def list_positions(self) -> list[dict]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT DISTINCT ON (currency_code)
                       currency_code, qty_after, rub_cost_after, created_at
                FROM firm_position_moves
                ORDER BY currency_code, id DESC
                """
            )
            return [dict(r) for r in rows]

    async def set_wallet_fact(
        self,
        *,
        currency_code: str,
        actual_qty: Decimal,
        comment: str | None = None,
        updated_by: int | None = None,
    ) -> None:
        """Ручной «фактический остаток кошелька» — для разрыва по валюте (4.3)."""
        async with self._connection() as con:
            await con.execute(
                """
                INSERT INTO firm_wallet_facts(currency_code, actual_qty, comment, updated_by, updated_at)
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (currency_code) DO UPDATE
                SET actual_qty = EXCLUDED.actual_qty,
                    comment = EXCLUDED.comment,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                """,
                self._norm_currency(currency_code),
                actual_qty,
                comment,
                updated_by,
            )

    async def get_wallet_facts(self) -> dict[str, Decimal]:
        async with self._connection() as con:
            rows = await con.fetch("SELECT currency_code, actual_qty FROM firm_wallet_facts")
            return {r["currency_code"]: Decimal(str(r["actual_qty"])) for r in rows}
