from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from domain import CityCode, Deal, DealEventPayload, DomainValidationError

from .deal_events import DealEvent, DealEventBus


def delivery_money(value: Decimal, *, positive: bool = True) -> Decimal:
    if (
        not value.is_finite()
        or value < 0
        or (positive and value == 0)
        or value >= Decimal("1e18")
        or value != value.quantize(Decimal("0.01"))
    ):
        raise DomainValidationError(
            "Укажите сумму RUB с точностью до копеек, больше нуля (вознаграждение может быть нулевым)"
        )
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateDelivery:
    client_id: int
    city: str
    amount: Decimal
    courier_tg_user_id: int
    courier_client_id: int
    reward: Decimal
    actor_user_id: int
    idempotency_key: str
    address: str = ""
    comment: str | None = None

    def __post_init__(self) -> None:
        delivery_money(self.amount)
        delivery_money(self.reward, positive=False)
        if (
            min(self.client_id, self.courier_tg_user_id, self.courier_client_id, self.actor_user_id)
            <= 0
            or not self.idempotency_key.strip()
        ):
            raise DomainValidationError("Выберите клиента, курьера и чат с его балансом")
        object.__setattr__(self, "city", str(CityCode(self.city)))


class DeliveryRepositoryPort(Protocol):
    async def couriers(self) -> list[dict[str, str | None]]: ...
    async def create(self, command: CreateDelivery) -> tuple[Deal, bool]: ...
    async def transition(
        self,
        deal_id: int,
        *,
        actor_user_id: int,
        status: str,
        expected_amount: Decimal,
        expected_reward: Decimal,
        expected_courier_client_id: int,
    ) -> tuple[Deal, bool]: ...


class DeliveryService:
    def __init__(self, repository: DeliveryRepositoryPort, events: DealEventBus) -> None:
        self._repository = repository
        self._events = events

    async def couriers(self) -> list[dict[str, str | None]]:
        return await self._repository.couriers()

    async def _publish(self, deal: Deal, changed: bool, event: str) -> Deal:
        if changed:
            await self._events.publish(
                DealEvent(
                    type=event,
                    deal_id=deal.id,
                    payload=DealEventPayload({"dealNo": deal.deal_no, "status": str(deal.status)}),
                )
            )
        return deal

    async def create(self, command: CreateDelivery) -> Deal:
        deal, changed = await self._repository.create(command)
        return await self._publish(deal, changed, "deal.created")

    async def transition(
        self,
        deal_id: int,
        *,
        actor_user_id: int,
        status: str,
        expected_amount: Decimal,
        expected_reward: Decimal,
        expected_courier_client_id: int,
    ) -> Deal:
        if status not in {"in_delivery", "done", "canceled"}:
            raise DomainValidationError("Недопустимый статус доставки")
        delivery_money(expected_amount)
        delivery_money(expected_reward, positive=False)
        deal, changed = await self._repository.transition(
            deal_id,
            actor_user_id=actor_user_id,
            status=status,
            expected_amount=expected_amount,
            expected_reward=expected_reward,
            expected_courier_client_id=expected_courier_client_id,
        )
        return await self._publish(deal, changed, "deal.status_changed")
