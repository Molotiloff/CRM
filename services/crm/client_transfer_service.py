from __future__ import annotations

from typing import Protocol

from domain import DealEventPayload

from .client_transfer_models import (
    ClientTransferAdjustmentCommand,
    ClientTransferAdjustmentResult,
    ClientTransferCommand,
    ClientTransferResult,
)
from .deal_events import DealEvent, DealEventBus

__all__ = [
    "ClientTransferAdjustmentCommand",
    "ClientTransferAdjustmentResult",
    "ClientTransferCommand",
    "ClientTransferResult",
    "ClientTransferService",
]


class ClientTransferRepositoryPort(Protocol):
    async def find_recipients(self, name: str) -> list[dict[str, int | str]]: ...

    async def transfer(self, command: ClientTransferCommand) -> ClientTransferResult: ...

    async def reject(self, command: ClientTransferCommand) -> bool: ...

    async def adjust(
        self, command: ClientTransferAdjustmentCommand
    ) -> ClientTransferAdjustmentResult: ...


class ClientTransferService:
    def __init__(
        self, repository: ClientTransferRepositoryPort, event_bus: DealEventBus
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus

    async def find_recipients(self, name: str) -> list[dict[str, int | str]]:
        return await self._repository.find_recipients(name)

    async def transfer(self, command: ClientTransferCommand) -> ClientTransferResult:
        result = await self._repository.transfer(command)
        if not result.repeated:
            await self._event_bus.publish(
                DealEvent(
                    type="deal.created",
                    deal_id=result.deal_id,
                    payload=DealEventPayload({"source_kind": "client_transfer"}),
                )
            )
        return result

    async def reject(self, command: ClientTransferCommand) -> bool:
        return await self._repository.reject(command)

    async def adjust(
        self, command: ClientTransferAdjustmentCommand
    ) -> ClientTransferAdjustmentResult:
        result = await self._repository.adjust(command)
        if not result.repeated:
            await self._event_bus.publish(DealEvent(
                type="deal.updated", deal_id=result.deal_id,
                payload=DealEventPayload({"source_kind": "client_transfer"}),
            ))
        return result
