from __future__ import annotations

from decimal import Decimal

import pytest

from api.presentation.deals import build_deal_details
from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.repositories.exchange_requests import ExchangeRequestsRepo
from domain import DomainStateError
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deal_type", "recv_code", "recv_amount", "pay_code", "pay_amount", "direction", "source"),
    [
        ("sale", "RUB", "1000", "USD", "12.5", "RUB → USD", "crm"),
        ("purchase", "USD", "12.5", "RUB", "1000", "USD → RUB", "tg_bot"),
    ],
)
async def test_exchange_table_completion_preserves_currency_direction(
    pool, client_id, deal_type, recv_code, recv_amount, pay_code, pay_amount, direction, source
) -> None:
    deal_repo = DealRepository(pool)
    request_repo = ExchangeRequestsRepo(pool)
    request_id = "12345678" if deal_type == "sale" else "12345679"
    table_id = "45678" if deal_type == "sale" else "45679"
    await request_repo.upsert_exchange_request_link(
        client_req_id=request_id,
        table_req_id=table_id,
        table_in_cur=recv_code,
        table_out_cur=pay_code,
        table_in_amount=Decimal(recv_amount),
        table_out_amount=Decimal(pay_amount),
        table_rate=Decimal("80"),
        status="active",
    )
    deal = await deal_repo.create_deal(DealCreateCommand(
        deal_type=deal_type,
        city="екб",
        actor_user_id=None,
        client_id=client_id,
        source=source,
        source_kind="exchange",
        source_ref=f"crm:{request_id}",
        exchange_client_req_id=request_id,
        body={
            "recv_code": recv_code, "recv_amount": recv_amount,
            "pay_code": pay_code, "pay_amount": pay_amount,
        },
    ))
    details = build_deal_details(deal)
    assert details.deal.direction == direction
    assert details.deal.asset == "USD"
    assert details.deal.amountRub == 1000.0
    assert details.deal.status == "new"
    if source == "crm":
        assert await deal_repo.get_by_source_ref(f"crm:{request_id}") is not None
    with pytest.raises(DomainStateError, match="не занесена"):
        await deal_repo.complete_exchange_after_table(table_id, actor_user_id=None)
    await request_repo.mark_exchange_request_table_done(table_req_id=table_id)
    done = await deal_repo.complete_exchange_after_table(table_id, actor_user_id=None)
    assert done is not None and done.status == "done"
    repeated = await deal_repo.complete_exchange_after_table(table_id, actor_user_id=None)
    assert repeated is not None and repeated.status == "done"
    assert [event.new_status for event in repeated.status_events] == ["new", "done"]
    async with pool.acquire() as con:
        outbox_count = await con.fetchval(
            "SELECT COUNT(*) FROM tg_outbox WHERE kind='deal_status_changed'"
        )
    assert outbox_count == 1
