from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import asyncpg
import pytest

from db_asyncpg.uow import AsyncpgUnitOfWork
from domain import DomainStateError
from services.accounting import (
    ManualCashAccountCode,
    ManualCashOperation,
    ManualCashService,
    RecordManualCashMove,
    ReverseManualCashMove,
)


@pytest.mark.asyncio
async def test_manual_cash_is_idempotent_and_reversed_by_a_new_move(pool) -> None:
    actor_id = await _seed(pool)
    service = ManualCashService(lambda: AsyncpgUnitOfWork(pool))
    command = RecordManualCashMove(
        account_code=ManualCashAccountCode.POETS,
        operation=ManualCashOperation.INFLOW,
        amount=Decimal("100.005"),
        effective_at=date(2026, 9, 2),
        comment="Корректировка наличных",
        actor_user_id=actor_id,
        idempotency_key="manual-cash:test:record",
    )

    first, repeated = await asyncio.gather(
        service.record(command),
        service.record(command),
    )
    reversal_command = ReverseManualCashMove(
        move_id=first.id,
        comment="Исправление опечатки",
        actor_user_id=actor_id,
        idempotency_key="manual-cash:test:reverse",
    )
    reversal = await service.reverse(reversal_command)
    repeated_reversal = await service.reverse(reversal_command)
    accounts, moves = await service.snapshot()

    assert first.amount == Decimal("100.01")
    assert repeated.id == first.id
    assert reversal.id == repeated_reversal.id
    assert reversal.reversal_of_id == first.id
    assert next(item for item in accounts if item.code is ManualCashAccountCode.POETS).balance == Decimal("0")
    assert len(moves) == 2
    assert next(item for item in moves if item.id == first.id).reversed is True

    async with pool.acquire() as connection:
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM cash_desk_moves WHERE idempotency_key=$1",
            command.idempotency_key,
        ) == 1
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await connection.execute(
                "UPDATE cash_desk_moves SET comment='changed' WHERE id=$1",
                first.id,
            )


@pytest.mark.asyncio
async def test_manual_cash_rejects_reused_idempotency_key_with_other_payload(pool) -> None:
    actor_id = await _seed(pool)
    service = ManualCashService(lambda: AsyncpgUnitOfWork(pool))
    original = RecordManualCashMove(
        account_code=ManualCashAccountCode.BS,
        operation=ManualCashOperation.OUTFLOW,
        amount=Decimal("10"),
        effective_at=date(2026, 9, 2),
        comment="Выдача",
        actor_user_id=actor_id,
        idempotency_key="manual-cash:test:collision",
    )
    changed = RecordManualCashMove(
        account_code=ManualCashAccountCode.BS,
        operation=ManualCashOperation.OUTFLOW,
        amount=Decimal("11"),
        effective_at=date(2026, 9, 2),
        comment="Выдача",
        actor_user_id=actor_id,
        idempotency_key=original.idempotency_key,
    )

    await service.record(original)
    with pytest.raises(DomainStateError, match="idempotency key"):
        await service.record(changed)


async def _seed(pool) -> int:
    async with pool.acquire() as connection:
        actor_id = await connection.fetchval(
            """
            INSERT INTO users(tg_user_id, display_name, role)
            VALUES (920021, 'Бухгалтер', 'accountant')
            ON CONFLICT (tg_user_id) DO UPDATE SET is_active=TRUE
            RETURNING id
            """
        )
        await connection.execute(
            """
            INSERT INTO cash_desks(city, name, currency_code)
            VALUES ('мск', 'Поэты', 'RUB'), ('мск', 'BS', 'RUB')
            ON CONFLICT (city, name, currency_code) DO NOTHING
            """
        )
    return int(actor_id)
