from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
import pytest

from db_asyncpg.uow import AsyncpgUnitOfWork
from domain import DomainStateError, FirmPositionMoveKind, NewFirmPositionMove
from services.accounting import (
    FirmPositionAccountingService,
    RecordOpening,
    RecordPurchase,
    RecordSale,
    ReversePositionMove,
)


async def test_position_service_persists_weighted_average_and_idempotency(pool) -> None:
    service = FirmPositionAccountingService(lambda: AsyncpgUnitOfWork(pool))
    opening = await service.record_opening(
        RecordOpening(
            currency="USDT",
            qty=Decimal("100"),
            rub_cost=Decimal("9000"),
            reason="approved cutover",
            idempotency_key="position:opening:usdt",
            effective_at=datetime(2026, 8, 19, tzinfo=UTC),
        )
    )
    purchase = await service.record_purchase(
        RecordPurchase(
            currency="USDT",
            qty=Decimal("100"),
            rate=Decimal("110"),
            idempotency_key="position:purchase:1",
        )
    )
    repeated = await service.record_purchase(
        RecordPurchase(
            currency="USDT",
            qty=Decimal("100"),
            rate=Decimal("110"),
            idempotency_key="position:purchase:1",
        )
    )
    sale = await service.record_sale(
        RecordSale(
            currency="USDT",
            qty=Decimal("50"),
            idempotency_key="position:sale:1",
        )
    )

    assert repeated.id == purchase.id
    assert sale.entry_rate == Decimal("100.00000000")
    assert sale.qty_after == Decimal("150.00000000")
    assert sale.rub_cost_after == Decimal("15000.00000000")
    assert opening.reason == "approved cutover"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM firm_position_moves") == 3


async def test_concurrent_purchases_are_serialized_by_currency_lock(pool) -> None:
    service = FirmPositionAccountingService(lambda: AsyncpgUnitOfWork(pool))

    await asyncio.gather(
        *(
            service.record_purchase(
                RecordPurchase(
                    currency="EUR",
                    qty=Decimal("1"),
                    rate=Decimal(str(90 + index)),
                    idempotency_key=f"position:concurrent:{index}",
                )
            )
            for index in range(10)
        )
    )

    position = await service.position("EUR")
    assert position.qty == Decimal("10.00000000")
    assert position.rub_cost == Decimal("945.00000000")


async def test_reversal_is_unique_and_journal_is_append_only(pool) -> None:
    service = FirmPositionAccountingService(lambda: AsyncpgUnitOfWork(pool))
    purchase = await service.record_purchase(
        RecordPurchase(
            currency="USD_BL",
            qty=Decimal("10"),
            rate=Decimal("90"),
            idempotency_key="position:purchase:reverse",
        )
    )
    sale = await service.record_sale(
        RecordSale(
            currency="USD_BL",
            qty=Decimal("5"),
            idempotency_key="position:sale:reverse",
        )
    )
    reversal = await service.reverse(
        ReversePositionMove(
            currency="USD_BL",
            move_id=sale.id,
            reason="source operation canceled",
            idempotency_key="position:reversal:1",
        )
    )

    assert reversal.qty_after == Decimal("10.00000000")
    assert reversal.rub_cost_after == Decimal("900.00000000")
    with pytest.raises(DomainStateError, match="already been reversed"):
        await service.reverse(
            ReversePositionMove(
                currency="USD_BL",
                move_id=sale.id,
                reason="duplicate reversal attempt",
                idempotency_key="position:reversal:2",
            )
        )

    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await connection.execute(
                "UPDATE firm_position_moves SET reason = 'changed' WHERE id = $1",
                purchase.id,
            )


async def test_uncommitted_position_move_rolls_back_with_unit_of_work(pool) -> None:
    service = FirmPositionAccountingService(lambda: AsyncpgUnitOfWork(pool))
    purchase = await service.record_purchase(
        RecordPurchase(
            currency="USD_WH",
            qty=Decimal("20"),
            rate=Decimal("95"),
            idempotency_key="position:committed",
        )
    )

    async with AsyncpgUnitOfWork(pool) as unit_of_work:
        assert unit_of_work.firm_positions is not None
        await unit_of_work.firm_positions.acquire_currency_lock(purchase.currency)
        current = await unit_of_work.firm_positions.get_current(purchase.currency)
        assert current.qty == Decimal("20.00000000")
        await unit_of_work.firm_positions.append(
            NewFirmPositionMove(
                currency=purchase.currency,
                kind=FirmPositionMoveKind.PURCHASE,
                qty=Decimal("1"),
                rub_amount=Decimal("95"),
                qty_after=Decimal("21"),
                rub_cost_after=Decimal("1995"),
                idempotency_key="position:rolled-back",
                effective_at=datetime.now(UTC),
            )
        )

    assert (await service.position("USD_WH")).qty == Decimal("20.00000000")
