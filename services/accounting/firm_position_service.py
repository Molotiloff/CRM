from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

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
from domain.accounting import accounting_decimal
from services.unit_of_work import UnitOfWorkFactory

from .models import (
    PositionCommand,
    RecordAdjustment,
    RecordOpening,
    RecordPurchase,
    RecordSale,
    ReversePositionMove,
)


class MoveCalculation(Protocol):
    def __call__(
        self,
        current: FirmPosition,
    ) -> tuple[Decimal, Decimal] | tuple[Decimal, Decimal, Decimal]: ...


class FirmPositionAccountingService:
    """Append-only accounting of the firm's weighted-average currency position."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._now = now_factory or (lambda: datetime.now(UTC))

    async def position(self, currency: CurrencyCode | str) -> FirmPosition:
        normalized = firm_position_currency(currency)
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.firm_positions.get_current(normalized)

    async def positions(self) -> list[FirmPosition]:
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.firm_positions.list_current()

    async def record_opening(self, command: RecordOpening) -> FirmPositionMove:
        qty = accounting_decimal(command.qty, field="opening quantity")
        cost = accounting_decimal(command.rub_cost, field="opening rub cost")
        if qty < 0 or cost < 0:
            raise DomainValidationError("Opening quantity and rub cost must not be negative")
        return await self._record(
            command,
            kind=FirmPositionMoveKind.OPENING,
            calculate=lambda current: self._opening(current, qty=qty, rub_cost=cost),
            expected_qty=qty,
            reason=command.reason,
        )

    async def record_purchase(self, command: RecordPurchase) -> FirmPositionMove:
        qty = self._positive(command.qty, field="purchase quantity")
        rate = self._positive(command.rate, field="purchase rate")
        return await self._record(
            command,
            kind=FirmPositionMoveKind.PURCHASE,
            calculate=lambda _current: (qty, qty * rate),
            expected_qty=qty,
        )

    async def record_sale(self, command: RecordSale) -> FirmPositionMove:
        qty = self._positive(command.qty, field="sale quantity")

        def calculate(current: FirmPosition) -> tuple[Decimal, Decimal, Decimal]:
            if qty > current.qty:
                raise DomainStateError("Firm position is insufficient for sale")
            entry_rate = current.average_rate
            rub_amount = current.rub_cost if qty == current.qty else qty * entry_rate
            return -qty, -rub_amount, entry_rate

        return await self._record(
            command,
            kind=FirmPositionMoveKind.SALE,
            calculate=calculate,
            expected_qty=-qty,
        )

    async def record_adjustment(self, command: RecordAdjustment) -> FirmPositionMove:
        qty = accounting_decimal(command.qty_delta, field="adjustment quantity")
        cost = accounting_decimal(command.rub_cost_delta, field="adjustment rub cost")
        if not str(command.reason).strip():
            raise DomainValidationError("Position adjustment reason is required")
        return await self._record(
            command,
            kind=FirmPositionMoveKind.ADJUST,
            calculate=lambda _current: (qty, cost),
            expected_qty=qty,
            reason=command.reason,
        )

    async def reverse(self, command: ReversePositionMove) -> FirmPositionMove:
        currency = firm_position_currency(command.currency)
        async with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.firm_positions
            await repository.acquire_currency_lock(currency)
            repeated = await repository.get_by_idempotency_key(command.idempotency_key)
            if repeated is not None:
                self._validate_replay(
                    repeated,
                    command=command,
                    currency=currency,
                    kind=FirmPositionMoveKind.REVERSAL,
                    expected_qty=repeated.qty,
                )
                if repeated.reversal_of_id != command.move_id:
                    raise DomainStateError("Idempotency key belongs to another reversal")
                return repeated
            original = await repository.get_move(command.move_id)
            if original is None:
                raise DomainValidationError("Position move to reverse was not found")
            if original.currency != currency:
                raise DomainValidationError("Reversal currency differs from source move")
            if original.kind is FirmPositionMoveKind.REVERSAL:
                raise DomainValidationError("A reversal move cannot be reversed")
            current = await repository.get_current(currency)
            move = self._new_move(
                command,
                current=current,
                kind=FirmPositionMoveKind.REVERSAL,
                qty=-original.qty,
                rub_amount=-original.rub_amount,
                reason=command.reason,
                reversal_of_id=original.id,
            )
            result = await repository.append(move)
            await unit_of_work.commit()
            return result

    async def _record(
        self,
        command: PositionCommand,
        *,
        kind: FirmPositionMoveKind,
        calculate: MoveCalculation,
        expected_qty: Decimal,
        reason: str | None = None,
    ) -> FirmPositionMove:
        currency = firm_position_currency(command.currency)
        async with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.firm_positions
            await repository.acquire_currency_lock(currency)
            repeated = await repository.get_by_idempotency_key(command.idempotency_key)
            if repeated is not None:
                self._validate_replay(
                    repeated,
                    command=command,
                    currency=currency,
                    kind=kind,
                    expected_qty=expected_qty,
                )
                return repeated
            current = await repository.get_current(currency)
            calculated = calculate(current)
            qty, rub_amount = calculated[:2]
            entry_rate = calculated[2] if len(calculated) == 3 else None
            move = self._new_move(
                command,
                current=current,
                kind=kind,
                qty=qty,
                rub_amount=rub_amount,
                entry_rate=entry_rate,
                reason=reason,
            )
            result = await repository.append(move)
            await unit_of_work.commit()
            return result

    def _new_move(
        self,
        command: PositionCommand,
        *,
        current: FirmPosition,
        kind: FirmPositionMoveKind,
        qty: Decimal,
        rub_amount: Decimal,
        entry_rate: Decimal | None = None,
        reason: str | None = None,
        reversal_of_id: int | None = None,
    ) -> NewFirmPositionMove:
        return NewFirmPositionMove(
            currency=current.currency,
            kind=kind,
            qty=qty,
            rub_amount=rub_amount,
            qty_after=current.qty + qty,
            rub_cost_after=current.rub_cost + rub_amount,
            idempotency_key=command.idempotency_key,
            effective_at=command.effective_at or self._now(),
            deal_id=command.deal_id,
            deal_leg_id=command.deal_leg_id,
            entry_rate=entry_rate,
            created_by=command.actor_user_id,
            reason=reason,
            reversal_of_id=reversal_of_id,
        )

    @staticmethod
    def _opening(
        current: FirmPosition,
        *,
        qty: Decimal,
        rub_cost: Decimal,
    ) -> tuple[Decimal, Decimal]:
        if current.qty != 0 or current.rub_cost != 0:
            raise DomainStateError("Opening can only be recorded for an empty position")
        return qty, rub_cost

    @staticmethod
    def _positive(value: object, *, field: str) -> Decimal:
        parsed = accounting_decimal(value, field=field)
        if parsed <= 0:
            raise DomainValidationError(f"{field.capitalize()} must be greater than zero")
        return parsed

    @staticmethod
    def _validate_replay(
        move: FirmPositionMove,
        *,
        command: PositionCommand,
        currency: CurrencyCode,
        kind: FirmPositionMoveKind,
        expected_qty: Decimal,
    ) -> None:
        if (
            move.currency != currency
            or move.kind is not kind
            or move.qty != expected_qty
            or move.deal_id != command.deal_id
            or move.deal_leg_id != command.deal_leg_id
        ):
            raise DomainStateError("Idempotency key belongs to another position operation")


FirmPositionService = FirmPositionAccountingService
