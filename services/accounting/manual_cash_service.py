from __future__ import annotations

from services.unit_of_work import UnitOfWorkFactory

from .models import (
    ManualCashAccount,
    ManualCashMove,
    RecordManualCashMove,
    ReverseManualCashMove,
)


class ManualCashService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def snapshot(
        self, *, limit: int = 20
    ) -> tuple[tuple[ManualCashAccount, ...], tuple[ManualCashMove, ...]]:
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.manual_cash.snapshot(limit=limit)
            await unit_of_work.commit()
            return result

    async def record(self, command: RecordManualCashMove) -> ManualCashMove:
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.manual_cash.record(command)
            await unit_of_work.commit()
            return result

    async def reverse(self, command: ReverseManualCashMove) -> ManualCashMove:
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.manual_cash.reverse(command)
            await unit_of_work.commit()
            return result
