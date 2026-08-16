from __future__ import annotations

from decimal import Decimal

import pytest

from db_asyncpg.repositories.deals import DealRepository
from services.crm.deal_service import (
    DealCreateCommand,
    DealListFilter,
    DealStatusCommand,
    DealUpdateCommand,
)


@pytest.mark.asyncio
async def test_deal_repository_persists_deal_and_status_history(pool, client_id) -> None:
    async with pool.acquire() as con:
        user_id = await con.fetchval(
            """
            INSERT INTO users (tg_user_id, display_name, role)
            VALUES (900001, 'Deal Manager', 'manager')
            ON CONFLICT (tg_user_id) DO UPDATE SET display_name = EXCLUDED.display_name
            RETURNING id
            """
        )
    repository = DealRepository(pool)

    created = await repository.create_deal(
        DealCreateCommand(
            deal_type="sale",
            city="Екб",
            actor_user_id=user_id,
            client_id=client_id,
            body={"currency": "USDT", "rub_amount": 125000},
            profit=Decimal("1500"),
        )
    )
    changed_result = await repository.change_status(
        created.id,
        DealStatusCommand(
            status="fixed",
            actor_user_id=user_id,
            payload={"rate": 81.25, "comment": "fixed"},
        ),
    )
    repeated_result = await repository.change_status(
        created.id,
        DealStatusCommand(status="fixed", actor_user_id=user_id),
    )
    listed = await repository.list_deals(DealListFilter(statuses=("fixed",)))

    assert changed_result is not None
    assert repeated_result is not None
    changed, was_changed = changed_result
    repeated, was_repeated = repeated_result
    assert was_changed is True
    assert was_repeated is False
    assert changed.status == "fixed"
    assert changed.client_name == "Тестовый чат"
    assert [event.new_status for event in changed.status_events] == ["new", "fixed"]
    assert repeated.status_events == changed.status_events
    assert [row.id for row in listed] == [created.id]


@pytest.mark.asyncio
async def test_deal_repository_idempotently_links_source_request(pool, client_id) -> None:
    repository = DealRepository(pool)
    command = DealCreateCommand(
        deal_type="deposit",
        city="екб",
        actor_user_id=None,
        client_id=client_id,
        source="tg_bot",
        source_kind="cash",
        source_ref="-100500:77",
        body={"req_id": "Б-123456", "amount": "1000"},
    )

    first, first_created = await repository.create_deal_idempotent(command)
    second, second_created = await repository.create_deal_idempotent(command)

    assert first_created is True
    assert second_created is False
    assert first.id == second.id
    assert first.source_ref == "-100500:77"
    assert [event.new_status for event in second.status_events] == ["new"]

    await repository.change_status(
        first.id,
        DealStatusCommand(status="fixed", actor_user_id=None, payload={"rate": "95.5"}),
    )
    await repository.change_status(
        first.id,
        DealStatusCommand(status="fixed", actor_user_id=None),
    )
    await repository.update_deal(
        first.id,
        DealUpdateCommand(body=first.body.merged({"amount": "1500"})),
    )
    async with pool.acquire() as con:
        outbox_rows = await con.fetch(
            "SELECT kind, payload::text AS payload, status FROM tg_outbox ORDER BY id"
        )

    assert len(outbox_rows) == 2
    assert outbox_rows[0]["kind"] == "deal_status_changed"
    assert outbox_rows[0]["status"] == "pending"
    assert '"newStatus": "fixed"' in outbox_rows[0]["payload"]
    assert outbox_rows[1]["kind"] == "deal_source_updated"
    assert '"status": "fixed"' in outbox_rows[1]["payload"]
