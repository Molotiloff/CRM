from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from domain import CityCode, Deal, DealEventPayload, DomainValidationError

from .deal_events import DealEvent, DealEventBus


def invoice_amount(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= 0 or value >= Decimal("1e18") or value != value.quantize(Decimal("0.00000001")):
        raise DomainValidationError("Укажите положительную сумму USDT, не более 8 знаков после запятой")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateInvoice:
    received: Decimal
    paid: Decimal
    city: str
    client_id: int
    actor_user_id: int
    idempotency_key: str
    comment: str | None = None

    def __post_init__(self) -> None:
        invoice_amount(self.received)
        invoice_amount(self.paid)
        if not self.city.strip() or not self.idempotency_key.strip() or self.client_id <= 0 or self.actor_user_id <= 0:
            raise DomainValidationError("Выберите клиента, город и идентификатор заявки")
        object.__setattr__(self, "city", str(CityCode(self.city)))


class InvoiceRepositoryPort(Protocol):
    async def create(self, command: CreateInvoice) -> tuple[Deal, bool]: ...
    async def post(self, deal_id: int, *, actor_user_id: int, expected_received: Decimal, expected_paid: Decimal, allow_loss: bool) -> tuple[Deal, bool]: ...
    async def cancel(self, deal_id: int, *, actor_user_id: int, comment: str | None) -> tuple[Deal, bool]: ...


class InvoiceService:
    def __init__(self, repository: InvoiceRepositoryPort, events: DealEventBus) -> None:
        self._repository = repository
        self._events = events

    async def _publish(self, deal: Deal, created: bool, event: str) -> Deal:
        if created:
            await self._events.publish(DealEvent(type=event, deal_id=deal.id, payload=DealEventPayload({"dealNo": deal.deal_no, "status": str(deal.status)})))
        return deal

    async def create(self, command: CreateInvoice) -> Deal:
        deal, created = await self._repository.create(command)
        return await self._publish(deal, created, "deal.created")

    async def post(self, deal_id: int, *, actor_user_id: int, expected_received: Decimal, expected_paid: Decimal, allow_loss: bool = False) -> Deal:
        invoice_amount(expected_received)
        invoice_amount(expected_paid)
        deal, changed = await self._repository.post(deal_id, actor_user_id=actor_user_id, expected_received=expected_received, expected_paid=expected_paid, allow_loss=allow_loss)
        return await self._publish(deal, changed, "deal.status_changed")

    async def cancel(self, deal_id: int, *, actor_user_id: int, comment: str | None = None) -> Deal:
        deal, changed = await self._repository.cancel(deal_id, actor_user_id=actor_user_id, comment=comment)
        return await self._publish(deal, changed, "deal.status_changed")
