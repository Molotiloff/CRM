from __future__ import annotations

from decimal import Decimal

from domain import DomainStateError, DomainValidationError
from services.accounting.models import (
    ManualCashAccount,
    ManualCashAccountCode,
    ManualCashMove,
    ManualCashOperation,
    RecordManualCashMove,
    ReverseManualCashMove,
)

from .base import ConnectionBoundRepo

_NAMES = {ManualCashAccountCode.POETS: "Поэты", ManualCashAccountCode.BS: "BS"}


class ManualCashRepo(ConnectionBoundRepo):
    async def snapshot(self, *, limit: int = 20):
        async with self._connection() as connection:
            accounts = await connection.fetch(
                "SELECT id, name, balance FROM cash_desks WHERE city='мск' AND currency_code='RUB' AND name=ANY($1::text[]) ORDER BY name",
                list(_NAMES.values()),
            )
            moves = await connection.fetch(
                """SELECT m.*, d.name, COALESCE(u.display_name, 'System') actor_name,
                          EXISTS(SELECT 1 FROM cash_desk_moves r WHERE r.reversal_of_id=m.id) reversed
                   FROM cash_desk_moves m JOIN cash_desks d ON d.id=COALESCE(m.from_desk_id,m.to_desk_id)
                   LEFT JOIN users u ON u.id=m.actor_user_id
                   WHERE d.city='мск' AND d.currency_code='RUB' AND d.name=ANY($1::text[])
                   ORDER BY m.effective_at DESC, m.id DESC LIMIT $2""",
                list(_NAMES.values()),
                limit,
            )
        by_name = {value: code for code, value in _NAMES.items()}
        return (
            tuple(
                ManualCashAccount(
                    code=by_name[row["name"]],
                    name=row["name"],
                    balance=Decimal(str(row["balance"])),
                )
                for row in accounts
            ),
            tuple(self._move(row, by_name[row["name"]]) for row in moves),
        )

    async def record(self, command: RecordManualCashMove) -> ManualCashMove:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('manual-cash:' || $1, 0))",
                command.account_code.value,
            )
            existing = await self._existing(connection, command.idempotency_key)
            if existing is not None:
                if (
                    existing.account_code != command.account_code
                    or existing.operation != command.operation.value
                    or existing.amount != command.amount
                    or existing.effective_at != command.effective_at
                    or existing.comment != command.comment
                ):
                    raise DomainStateError(
                        "Manual cash idempotency key was reused with another payload"
                    )
                return existing
            desk = await self._desk(connection, command.account_code)
            delta = (
                command.amount
                if command.operation is ManualCashOperation.INFLOW
                else -command.amount
            )
            balance = Decimal(str(desk["balance"])) + delta
            move_id = await connection.fetchval(
                """INSERT INTO cash_desk_moves(from_desk_id,to_desk_id,amount,balance_after_from,balance_after_to,actor_user_id,comment,operation_kind,effective_at,idempotency_key)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id""",
                desk["id"] if delta < 0 else None,
                desk["id"] if delta > 0 else None,
                command.amount,
                balance if delta < 0 else None,
                balance if delta > 0 else None,
                command.actor_user_id,
                command.comment,
                command.operation.value,
                command.effective_at,
                command.idempotency_key,
            )
            await connection.execute(
                "UPDATE cash_desks SET balance=$2 WHERE id=$1", desk["id"], balance
            )
            return await self._by_id(connection, int(move_id))

    async def reverse(self, command: ReverseManualCashMove) -> ManualCashMove:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('manual-cash-reversal:' || $1, 0))",
                command.idempotency_key,
            )
            existing = await self._existing(connection, command.idempotency_key)
            if existing is not None:
                if existing.reversal_of_id != command.move_id:
                    raise DomainStateError(
                        "Manual cash idempotency key was reused with another payload"
                    )
                return existing
            original = await connection.fetchrow(
                "SELECT * FROM cash_desk_moves WHERE id=$1 FOR UPDATE", command.move_id
            )
            if original is None or original["operation_kind"] not in {"inflow", "outflow"}:
                raise DomainValidationError("Reversible manual cash move was not found")
            if await connection.fetchval(
                "SELECT 1 FROM cash_desk_moves WHERE reversal_of_id=$1", command.move_id
            ):
                raise DomainStateError("Manual cash move is already reversed")
            desk_id = original["from_desk_id"] or original["to_desk_id"]
            desk = await connection.fetchrow(
                "SELECT id,name,balance FROM cash_desks WHERE id=$1 FOR UPDATE", desk_id
            )
            original_delta = Decimal(str(original["amount"])) * (
                -1 if original["from_desk_id"] else 1
            )
            balance = Decimal(str(desk["balance"])) - original_delta
            move_id = await connection.fetchval(
                """INSERT INTO cash_desk_moves(from_desk_id,to_desk_id,amount,balance_after_from,balance_after_to,actor_user_id,comment,operation_kind,effective_at,idempotency_key,reversal_of_id)
                   VALUES($1,$2,$3,$4,$5,$6,$7,'reversal',CURRENT_DATE,$8,$9) RETURNING id""",
                desk_id if original_delta > 0 else None,
                desk_id if original_delta < 0 else None,
                original["amount"],
                balance if original_delta > 0 else None,
                balance if original_delta < 0 else None,
                command.actor_user_id,
                command.comment.strip(),
                command.idempotency_key,
                command.move_id,
            )
            await connection.execute(
                "UPDATE cash_desks SET balance=$2 WHERE id=$1", desk_id, balance
            )
            return await self._by_id(connection, int(move_id))

    async def _desk(self, connection, code):
        row = await connection.fetchrow(
            "SELECT id,name,balance FROM cash_desks WHERE city='мск' AND name=$1 AND currency_code='RUB' AND is_active FOR UPDATE",
            _NAMES[code],
        )
        if row is None:
            raise DomainStateError("Manual cash account is unavailable")
        return row

    async def _existing(self, connection, key):
        row = await connection.fetchrow(
            """SELECT m.*,d.name,COALESCE(u.display_name,'System') actor_name,FALSE reversed FROM cash_desk_moves m JOIN cash_desks d ON d.id=COALESCE(m.from_desk_id,m.to_desk_id) LEFT JOIN users u ON u.id=m.actor_user_id WHERE m.idempotency_key=$1""",
            key,
        )
        return self._move(row, {v: k for k, v in _NAMES.items()}[row["name"]]) if row else None

    async def _by_id(self, connection, move_id):
        row = await connection.fetchrow(
            """SELECT m.*,d.name,COALESCE(u.display_name,'System') actor_name,FALSE reversed FROM cash_desk_moves m JOIN cash_desks d ON d.id=COALESCE(m.from_desk_id,m.to_desk_id) LEFT JOIN users u ON u.id=m.actor_user_id WHERE m.id=$1""",
            move_id,
        )
        return self._move(row, {v: k for k, v in _NAMES.items()}[row["name"]])

    @staticmethod
    def _move(row, code):
        balance = row["balance_after_from"] if row["from_desk_id"] else row["balance_after_to"]
        return ManualCashMove(
            id=row["id"],
            account_code=code,
            operation=row["operation_kind"],
            amount=Decimal(str(row["amount"])),
            balance_after=Decimal(str(balance)),
            effective_at=row["effective_at"],
            comment=row["comment"] or "",
            actor_name=row["actor_name"],
            reversal_of_id=row["reversal_of_id"],
            reversed=bool(row["reversed"]),
        )
