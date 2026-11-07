import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial

import pytest

from db_asyncpg.repositories.deal_workflow import DealWorkflowRepository
from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.repositories.fulfillment_queue import FulfillmentQueueRepo
from db_asyncpg.uow import AsyncpgUnitOfWork
from domain import DomainStateError, DomainValidationError
from services.accounting.fulfillment_models import (
    EnqueueFulfillment,
    FulfillmentRequestKind,
    ReorderFulfillment,
)
from services.crm.deal_events import DealEventBus
from services.payment_watch.settlement_models import ConfirmedTransfer
from services.payment_watch.settlement_service import DealSettlementService, SettlementError
from tests.db.conftest import balance_of
from tests.db.test_deal_source_mutation_uow import _seed_exchange, _source_mutation_service
from tests.db.test_fulfillment_queue_service import _actor_id, _service, _start


@pytest.mark.parametrize("watch_status", ["STOPPED", "TIMED_OUT", "WATCHING"])
async def test_cancel_otpr_closes_watch_and_queue_without_refund(pool, repo, client_id, watch_status):
    queue = _service(pool)
    started = await queue.start_client_withdrawal(_start(chat_id=-100500, suffix=watch_status, requested_qty=Decimal("25")))
    user_id = await _actor_id(pool)
    async with pool.acquire() as con:
        await con.execute("UPDATE payment_watches SET status=$2 WHERE id=$1", started.watch_id, watch_status)
    before = await balance_of(repo, client_id, "USDT")
    mutation = _source_mutation_service(pool, DealEventBus())
    canceled = await mutation.cancel(started.item.deal_id, actor_user_id=user_id, comment="Не отправляли")
    repeated = await mutation.cancel(started.item.deal_id, actor_user_id=user_id)
    assert str(canceled.status) == str(repeated.status) == "canceled"
    assert await balance_of(repo, client_id, "USDT") == before
    assert await queue.list_active() == []
    assert (await queue.summary()).queued_qty == 0
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT status FROM payment_watches WHERE id=$1", started.watch_id) == "STOPPED"
        assert await con.fetchval("SELECT status FROM usdt_fulfillment_queue WHERE id=$1", started.item.id) == "canceled"
        assert await con.fetchval("SELECT count(*) FROM deal_status_events WHERE deal_id=$1 AND new_status='canceled'", started.item.deal_id) == 1
    # A poller may have fetched this watch just before cancellation committed.
    settlement = DealSettlementService(partial(AsyncpgUnitOfWork, pool), fulfillment_queue_service=queue)
    transfer = ConfirmedTransfer(watch_id=started.watch_id, tx_hash="late-after-cancel", direction="IN", amount=Decimal("25"), token_symbol="USDT", confirmations=1, block_ts=datetime.now(UTC), from_address="TOurAddress", to_address="TClientAddress")
    for action in (settlement.settle, settlement.settle_test):
        with pytest.raises(SettlementError, match="отменена"):
            await action(transfer)
    assert await balance_of(repo, client_id, "USDT") == before


async def test_detected_test_payment_prevents_cancellation_and_refund(pool, repo, client_id):
    queue = _service(pool)
    started = await queue.start_client_withdrawal(_start(chat_id=-100500, suffix="test", requested_qty=Decimal("25")))
    user_id = await _actor_id(pool)
    async with pool.acquire() as con:
        await con.execute("INSERT INTO payment_watch_events(watch_id, tx_hash, event_type, direction, amount, token_symbol, confirmations, block_ts) VALUES($1,'test-payment','TEST','IN',1,'USDT',1,NOW())", started.watch_id)
    before = await balance_of(repo, client_id, "USDT")
    with pytest.raises(DomainStateError, match="обнаружена транзакция"):
        await _source_mutation_service(pool, DealEventBus()).cancel(started.item.deal_id, actor_user_id=user_id)
    assert await balance_of(repo, client_id, "USDT") == before
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT status FROM deals WHERE id=$1", started.item.deal_id) == "awaiting_payment"
        assert await con.fetchval("SELECT status FROM payment_watches WHERE id=$1", started.watch_id) == "WATCHING"
        assert await con.fetchval("SELECT status FROM usdt_fulfillment_queue WHERE id=$1", started.item.id) == "executing"


@pytest.mark.parametrize("terminal", ["canceled", "done"])
@pytest.mark.parametrize("queue_status", ["queued", "executing"])
async def test_legacy_terminal_deals_are_excluded_from_all_queue_views(pool, repo, client_id, terminal, queue_status):
    _, old = await _seed_exchange(pool, repo, client_id, suffix="OLD1", source="crm")
    _, active = await _seed_exchange(pool, repo, client_id, suffix="NEW2", source="crm")
    queue = _service(pool)
    old_item = await queue.enqueue(EnqueueFulfillment(deal_id=old.id, request_kind=FulfillmentRequestKind.SALE, qty=Decimal("100")))
    active_item = await queue.enqueue(EnqueueFulfillment(deal_id=active.id, request_kind=FulfillmentRequestKind.SALE, qty=Decimal("20")))
    async with pool.acquire() as con:
        await con.execute("UPDATE deals SET status=$2 WHERE id=$1", old.id, terminal)
        await con.execute("UPDATE usdt_fulfillment_queue SET status=$2 WHERE id=$1", old_item.id, queue_status)
    assert [item.id for item in await queue.list_active()] == [active_item.id]
    assert (await queue.summary()).queued_qty == 20
    workflow = await DealWorkflowRepository(pool).get_fulfillment_balance_check(active.id)
    assert workflow["queued_qty"] == 20
    repository = FulfillmentQueueRepo(pool)
    assert await repository.executing_qty() == 0
    async with AsyncpgUnitOfWork(pool) as uow:
        selected = await uow.fulfillment_queue.next_for_client_for_update(chat_id=-100500)
        assert selected.id == active_item.id
    with pytest.raises(DomainValidationError, match="Active fulfillment item was not found"):
        await queue.reorder(ReorderFulfillment(item_id=old_item.id, before_item_id=None, actor_user_id=None))


async def test_crm_exchange_cancel_removes_attached_fulfillment(pool, repo, client_id):
    _, deal = await _seed_exchange(pool, repo, client_id, suffix="CANCEL1", source="crm")
    queue = _service(pool)
    item = await queue.enqueue(EnqueueFulfillment(deal_id=deal.id, request_kind=FulfillmentRequestKind.SALE, qty=Decimal("100")))
    user_id = await _actor_id(pool)
    await _source_mutation_service(pool, DealEventBus()).cancel(deal.id, actor_user_id=user_id)
    assert await queue.list_active() == []
    assert await balance_of(repo, client_id, "USDT") == 0
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT status FROM usdt_fulfillment_queue WHERE id=$1", item.id) == "canceled"


async def test_cancel_failure_rolls_back_watch_and_queue(pool, client_id, monkeypatch):
    started = await _service(pool).start_client_withdrawal(_start(chat_id=-100500, suffix="rollback", requested_qty=Decimal("25")))
    actor = await _actor_id(pool)

    async def fail_status(*args, **kwargs):
        raise RuntimeError("status write failed")

    monkeypatch.setattr(DealRepository, "change_status", fail_status)
    with pytest.raises(RuntimeError, match="status write failed"):
        await _source_mutation_service(pool, DealEventBus()).cancel(started.item.deal_id, actor_user_id=actor)
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT status FROM deals WHERE id=$1", started.item.deal_id) == "awaiting_payment"
        assert await con.fetchval("SELECT status FROM payment_watches WHERE id=$1", started.watch_id) == "WATCHING"
        assert await con.fetchval("SELECT status FROM usdt_fulfillment_queue WHERE id=$1", started.item.id) == "executing"


async def test_concurrent_cancel_and_confirmation_cannot_both_commit(pool, repo, client_id):
    queue = _service(pool)
    started = await queue.start_client_withdrawal(_start(chat_id=-100500, suffix="race", requested_qty=Decimal("25")))
    actor = await _actor_id(pool)
    settlement = DealSettlementService(partial(AsyncpgUnitOfWork, pool), fulfillment_queue_service=queue)
    transfer = ConfirmedTransfer(watch_id=started.watch_id, tx_hash="racing-confirmation", direction="IN", amount=Decimal("25"), token_symbol="USDT", confirmations=1, block_ts=datetime.now(UTC), from_address="TOurAddress", to_address="TClientAddress")
    results = await asyncio.wait_for(asyncio.gather(
        _source_mutation_service(pool, DealEventBus()).cancel(started.item.deal_id, actor_user_id=actor),
        settlement.settle(transfer), return_exceptions=True,
    ), timeout=10)
    errors = [result for result in results if isinstance(result, Exception)]
    assert len(errors) == 1
    assert isinstance(errors[0], DomainStateError | DomainValidationError | SettlementError)
    async with pool.acquire() as con:
        status = await con.fetchval("SELECT status FROM deals WHERE id=$1", started.item.deal_id)
        item_status = await con.fetchval("SELECT status FROM usdt_fulfillment_queue WHERE id=$1", started.item.id)
    assert (status, item_status) in {("canceled", "canceled"), ("done", "completed")}
    assert await balance_of(repo, client_id, "USDT") == (0 if status == "canceled" else -25)
