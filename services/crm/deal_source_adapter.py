from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from domain import Deal, ExchangeRequestSource, ScheduleEntry, SourceKind
from services.unit_of_work import UnitOfWorkPort

from .deal_service import DealUpdateCommand, DealValidationError
from .deal_source_commands import DealSourceEditCommand


class DealSourceRepositoryPort(Protocol):
    async def get_exchange_source(self, *, request_id: str) -> ExchangeRequestSource | None: ...

    async def get_schedule_entry(self, *, request_id: str) -> ScheduleEntry | None: ...


class DealSourceEditPlan(Protocol):
    @property
    def update_command(self) -> DealUpdateCommand: ...

    async def apply(self, unit_of_work: UnitOfWorkPort) -> None: ...


class DealSourceAdapter(Protocol):
    source_kind: SourceKind

    async def prepare_edit(
        self,
        deal: Deal,
        command: DealSourceEditCommand,
        *,
        actor_name: str,
    ) -> DealSourceEditPlan: ...

    async def cancel(self, unit_of_work: UnitOfWorkPort, deal: Deal) -> None: ...


class DealSourceAdapterRegistry:
    def __init__(self, adapters: Iterable[DealSourceAdapter]) -> None:
        self._adapters: dict[SourceKind, DealSourceAdapter] = {}
        for adapter in adapters:
            if adapter.source_kind in self._adapters:
                raise DealValidationError(
                    f"Duplicate deal source adapter: {adapter.source_kind}"
                )
            self._adapters[adapter.source_kind] = adapter

    def require(self, source_kind: SourceKind | None) -> DealSourceAdapter:
        adapter = self._adapters.get(source_kind) if source_kind is not None else None
        if adapter is None:
            raise DealValidationError(f"Unsupported Telegram source kind: {source_kind}")
        return adapter
