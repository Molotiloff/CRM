from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from domain import (
    CurrencyCode,
    DomainStateError,
    DomainValidationError,
    FirmPosition,
    FirmPositionMove,
    FirmPositionMoveKind,
    NewFirmPositionMove,
    firm_position_currency,
)
from services.accounting import (
    FirmPositionAccountingService,
    RecordAdjustment,
    RecordOpening,
    RecordPurchase,
    RecordSale,
    ReversePositionMove,
)

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


class FakeFirmPositionRepository:
    def __init__(self) -> None:
        self.moves: list[FirmPositionMove] = []
        self.locked: list[CurrencyCode] = []

    async def acquire_currency_lock(self, currency: CurrencyCode) -> None:
        self.locked.append(currency)

    async def get_current(self, currency: CurrencyCode) -> FirmPosition:
        matching = [move for move in self.moves if move.currency == currency]
        return matching[-1].position if matching else FirmPosition(currency, Decimal(0), Decimal(0))

    async def list_current(self) -> list[FirmPosition]:
        result: dict[CurrencyCode, FirmPosition] = {}
        for move in self.moves:
            result[move.currency] = move.position
        return list(result.values())

    async def append(self, move: NewFirmPositionMove) -> FirmPositionMove:
        if move.reversal_of_id is not None and any(
            item.reversal_of_id == move.reversal_of_id for item in self.moves
        ):
            raise RuntimeError("duplicate reversal")
        persisted = FirmPositionMove(
            id=len(self.moves) + 1,
            currency=move.currency,
            kind=move.kind,
            qty=move.qty,
            rub_amount=move.rub_amount,
            qty_after=move.qty_after,
            rub_cost_after=move.rub_cost_after,
            idempotency_key=move.idempotency_key,
            effective_at=move.effective_at,
            created_at=NOW,
            deal_id=move.deal_id,
            deal_leg_id=move.deal_leg_id,
            entry_rate=move.entry_rate,
            created_by=move.created_by,
            reason=move.reason,
            reversal_of_id=move.reversal_of_id,
        )
        self.moves.append(persisted)
        return persisted

    async def get_by_idempotency_key(self, key: str) -> FirmPositionMove | None:
        return next((move for move in self.moves if move.idempotency_key == key), None)

    async def get_move(self, move_id: int) -> FirmPositionMove | None:
        return next((move for move in self.moves if move.id == move_id), None)


class FakeUnitOfWork:
    def __init__(self, repository: FakeFirmPositionRepository) -> None:
        self.firm_positions = repository
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return None


class FakeUnitOfWorkFactory:
    def __init__(self) -> None:
        self.repository = FakeFirmPositionRepository()
        self.instances: list[FakeUnitOfWork] = []

    def __call__(self) -> FakeUnitOfWork:
        unit_of_work = FakeUnitOfWork(self.repository)
        self.instances.append(unit_of_work)
        return unit_of_work


def service_and_factory() -> tuple[FirmPositionAccountingService, FakeUnitOfWorkFactory]:
    factory = FakeUnitOfWorkFactory()
    return FirmPositionAccountingService(factory, now_factory=lambda: NOW), factory


def test_firm_position_currency_accepts_only_canonical_codes() -> None:
    assert firm_position_currency(" usdt ") == CurrencyCode("USDT")
    with pytest.raises(DomainValidationError, match="Unsupported"):
        firm_position_currency("USDW")


async def test_purchase_and_sale_preserve_weighted_average_rate() -> None:
    service, factory = service_and_factory()
    await service.record_purchase(
        RecordPurchase(currency="USDT", qty=Decimal("100"), rate=Decimal("90"), idempotency_key="p1")
    )
    await service.record_purchase(
        RecordPurchase(currency="USDT", qty=Decimal("100"), rate=Decimal("110"), idempotency_key="p2")
    )

    sale = await service.record_sale(
        RecordSale(currency="USDT", qty=Decimal("50"), idempotency_key="s1")
    )

    assert sale.entry_rate == Decimal("100")
    assert sale.position == FirmPosition(CurrencyCode("USDT"), Decimal("150"), Decimal("15000"))
    assert sale.position.average_rate == Decimal("100")
    assert factory.repository.locked == [CurrencyCode("USDT")] * 3


async def test_full_sale_clears_fractional_cost_tail() -> None:
    service, _ = service_and_factory()
    await service.record_opening(
        RecordOpening(
            currency="EUR",
            qty=Decimal("3"),
            rub_cost=Decimal("10"),
            reason="migration",
            idempotency_key="opening",
        )
    )

    sale = await service.record_sale(
        RecordSale(currency="EUR", qty=Decimal("3"), idempotency_key="sale")
    )

    assert sale.entry_rate == Decimal("10") / Decimal("3")
    assert sale.qty_after == 0
    assert sale.rub_cost_after == 0


async def test_duplicate_idempotency_key_returns_original_move() -> None:
    service, factory = service_and_factory()
    command = RecordPurchase(
        currency="USD_BL",
        qty=Decimal("25"),
        rate=Decimal("91"),
        idempotency_key="purchase:one",
    )

    first = await service.record_purchase(command)
    repeated = await service.record_purchase(command)

    assert repeated == first
    assert len(factory.repository.moves) == 1

    with pytest.raises(DomainStateError, match="another position operation"):
        await service.record_purchase(
            RecordPurchase(
                currency="USD_BL",
                qty=Decimal("30"),
                rate=Decimal("91"),
                idempotency_key="purchase:one",
            )
        )


async def test_sale_and_adjustment_cannot_make_position_negative() -> None:
    service, _ = service_and_factory()
    await service.record_purchase(
        RecordPurchase(currency="USD_WH", qty=Decimal("5"), rate=Decimal("90"), idempotency_key="p")
    )

    with pytest.raises(DomainStateError, match="insufficient"):
        await service.record_sale(
            RecordSale(currency="USD_WH", qty=Decimal("6"), idempotency_key="s")
        )
    with pytest.raises(DomainStateError, match="must not be negative"):
        await service.record_adjustment(
            RecordAdjustment(
                currency="USD_WH",
                qty_delta=Decimal("-6"),
                rub_cost_delta=Decimal("-450"),
                reason="audit correction",
                idempotency_key="a",
            )
        )


async def test_reversal_is_compensating_move_and_requires_reason() -> None:
    service, factory = service_and_factory()
    purchase = await service.record_purchase(
        RecordPurchase(currency="USDT", qty=Decimal("10"), rate=Decimal("90"), idempotency_key="p")
    )

    reversal = await service.reverse(
        ReversePositionMove(
            currency="USDT",
            move_id=purchase.id,
            reason="duplicate source operation",
            idempotency_key="reverse:p",
        )
    )

    assert reversal.kind is FirmPositionMoveKind.REVERSAL
    assert reversal.reversal_of_id == purchase.id
    assert reversal.qty_after == 0
    assert reversal.rub_cost_after == 0
    assert len(factory.repository.moves) == 2

    with pytest.raises(DomainValidationError, match="reason"):
        await service.reverse(
            ReversePositionMove(
                currency="USDT",
                move_id=purchase.id,
                reason="",
                idempotency_key="reverse:reversal",
            )
        )
