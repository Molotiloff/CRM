from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain import Deal, DealBody, DealEventPayload, DealStatus
from services.crm.deal_events import DealEventBus
from services.crm.deal_service import (
    DealCreateCommand,
    DealService,
    DealStatusCommand,
    DealUpdateCommand,
)


@pytest.mark.asyncio
async def test_deal_service_publishes_created_and_status_events() -> None:
    repository = FakeDealRepository()
    event_bus = DealEventBus()
    service = DealService(repository, event_bus)

    async with event_bus.subscribe() as queue:
        created = await service.create_deal(
            DealCreateCommand(deal_type="sale", city="Екб", actor_user_id=1)
        )
        changed = await service.change_status(
            1,
            DealStatusCommand(
                status="fixed", actor_user_id=1, payload={"rate": 81.5}
            ),
        )

        assert created.status == "new"
        assert changed.status == "fixed"
        assert (await queue.get()).type == "deal.created"
        status_event = await queue.get()
        assert status_event.type == "deal.status_changed"
        assert status_event.payload.get("status") == "fixed"


@pytest.mark.asyncio
async def test_source_deal_does_not_publish_duplicate_created_event() -> None:
    repository = FakeDealRepository()
    event_bus = DealEventBus()
    service = DealService(repository, event_bus)
    command = DealCreateCommand(
        deal_type="sale",
        city="Екб",
        actor_user_id=None,
        source="tg_bot",
        source_kind="exchange",
        source_ref="-100:42",
    )

    async with event_bus.subscribe() as queue:
        first = await service.create_source_deal(command)
        second = await service.create_source_deal(command)

        assert first.id == second.id
        assert (await queue.get()).type == "deal.created"
        assert queue.empty()


def test_deal_write_commands_normalize_mutable_transport_values() -> None:
    body = {"amount": "100"}
    payload = {"comment": "fixed"}

    update = DealUpdateCommand(city=" ЕКБ ", body=body)
    status = DealStatusCommand(
        status="fixed",
        actor_user_id=1,
        payload=payload,
    )
    body["amount"] = "200"
    payload["comment"] = "changed"

    assert update.to_changes()["city"] == "екб"
    assert isinstance(update.body, DealBody)
    assert update.body.get("amount") == "100"
    assert status.status is DealStatus.FIXED
    assert isinstance(status.payload, DealEventPayload)
    assert status.payload.get("comment") == "fixed"


class FakeDealRepository:
    def __init__(self) -> None:
        self.row = _deal_row()
        self.source_created = False

    async def list_deals(self, filters):
        return [Deal.from_record(self.row)]

    async def get_deal(self, deal_id: int):
        return Deal.from_record(self.row) if deal_id == 1 else None

    async def create_deal(self, command):
        return Deal.from_record(self.row)

    async def create_deal_idempotent(self, command):
        created = not self.source_created
        self.source_created = True
        return Deal.from_record(self.row), created

    async def update_deal(self, deal_id: int, command):
        self.row.update(command.to_changes())
        return Deal.from_record(self.row)

    async def change_status(
        self, deal_id: int, command
    ):
        self.row["status"] = command.status
        return Deal.from_record(self.row), True


def _deal_row() -> dict:
    now = datetime.now(UTC)
    return {
        "id": 1,
        "deal_no": 100001,
        "deal_type": "sale",
        "city": "Екб",
        "status": "new",
        "body": {"currency": "USDT", "rub_amount": 1000},
        "created_at": now,
        "updated_at": now,
    }
