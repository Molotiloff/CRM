from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial

import pytest

from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.repositories.exchange_requests import ExchangeRequestsRepo
from db_asyncpg.uow import AsyncpgUnitOfWork
from domain import DomainStateError, SettlementReviewStatus
from domain.accounting_flows import SettlementResolution
from services.accounting.firm_position_service import FirmPositionAccountingService
from services.accounting.fulfillment_models import (
    EnqueueFulfillment,
    FulfillmentRequestKind,
    FulfillmentStatus,
    ReorderFulfillment,
    StartFulfillmentExecution,
)
from services.accounting.fulfillment_service import FulfillmentQueueService
from services.accounting.models import RecordOpening
from services.crm.deal_service import DealCreateCommand
from services.payment_watch.settlement_models import (
    ConfirmedTransfer,
    ResolveSettlementCommand,
)
from services.payment_watch.settlement_service import DealSettlementService


async def test_fifo_is_shared_by_kinds_and_manual_reorder_is_stable(
    pool, client_id
) -> None:
    deal_ids = [await _deal(pool, client_id, suffix=str(index)) for index in range(3)]
    service = _service(pool)
    first = await service.enqueue(
        EnqueueFulfillment(
            deal_id=deal_ids[0],
            request_kind=FulfillmentRequestKind.SALE,
            qty=Decimal("10"),
        )
    )
    second = await service.enqueue(
        EnqueueFulfillment(
            deal_id=deal_ids[1],
            request_kind=FulfillmentRequestKind.CLIENT_WITHDRAWAL,
            qty=Decimal("20"),
        )
    )
    third = await service.enqueue(
        EnqueueFulfillment(
            deal_id=deal_ids[2],
            request_kind=FulfillmentRequestKind.SALE,
            qty=Decimal("30"),
        )
    )

    assert [item.id for item in await service.list_active()] == [
        first.id,
        second.id,
        third.id,
    ]
    moved = await service.reorder(
        ReorderFulfillment(
            item_id=third.id,
            before_item_id=first.id,
            actor_user_id=await _actor_id(pool),
        )
    )

    assert moved.reordered_at is not None
    assert moved.reordered_by is not None
    assert [item.id for item in await service.list_active()] == [
        third.id,
        first.id,
        second.id,
    ]


async def test_enqueue_is_idempotent_and_rejects_changed_active_terms(
    pool, client_id
) -> None:
    deal_id = await _deal(pool, client_id, suffix="idempotent")
    service = _service(pool)
    command = EnqueueFulfillment(
        deal_id=deal_id,
        request_kind=FulfillmentRequestKind.SALE,
        qty=Decimal("15"),
    )

    first = await service.enqueue(command)
    repeated = await service.enqueue(command)

    assert repeated.id == first.id
    with pytest.raises(DomainStateError, match="another active fulfillment"):
        await service.enqueue(
            EnqueueFulfillment(
                deal_id=deal_id,
                request_kind=FulfillmentRequestKind.SALE,
                qty=Decimal("16"),
            )
        )


async def test_pending_queue_only_affects_summary_not_fact_or_position(
    pool, client_id
) -> None:
    deal_id = await _deal(pool, client_id, suffix="summary")
    service = _service(pool)
    item = await service.enqueue(
        EnqueueFulfillment(
            deal_id=deal_id,
            request_kind=FulfillmentRequestKind.SALE,
            qty=Decimal("70"),
        )
    )
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO firm_wallet_fact_snapshots(
                currency_code, actual_qty, observed_at, source, idempotency_key
            )
            VALUES ('USDT', 50, $1, 'manual', 'queue-summary-fact')
            """,
            datetime.now(UTC),
        )

    summary = await service.summary()

    assert summary.queued_qty == Decimal("70")
    assert summary.usdt_fact == Decimal("50")
    assert summary.queue_shortage_qty == Decimal("20")
    assert summary.onchain_liquid_qty == Decimal("0")
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT COUNT(*) FROM firm_position_moves") == 0
        assert await connection.fetchval(
            """
            SELECT actual_qty FROM firm_wallet_fact_snapshots
            WHERE idempotency_key = 'queue-summary-fact'
            """
        ) == Decimal("50")

    canceled = await service.cancel(item_id=item.id, reason="client canceled")
    assert canceled.status is FulfillmentStatus.CANCELED
    assert (await service.summary()).queued_qty == Decimal("0")


async def test_shortage_still_starts_tronscan_watch_without_accounting_moves(
    pool, client_id
) -> None:
    deal_id = await _deal(pool, client_id, suffix="shortage")
    service = _service(pool)
    item = await service.enqueue(
        EnqueueFulfillment(
            deal_id=deal_id,
            request_kind=FulfillmentRequestKind.SALE,
            qty=Decimal("70"),
        )
    )
    await _wallet_fact(pool, qty=Decimal("50"), key="shortage-fact")

    started = await service.start_execution(
        _start(chat_id=-100500, suffix="shortage")
    )

    active = await service.list_active()
    assert active[0].id == item.id
    assert active[0].status is FulfillmentStatus.EXECUTING
    assert started.watch_id > 0
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT COUNT(*) FROM payment_watches") == 1
        assert await connection.fetchval("SELECT COUNT(*) FROM firm_position_moves") == 0


async def test_concurrent_starts_are_not_blocked_by_stale_wallet_fact(
    pool, repo, client_id
) -> None:
    second_client_id = await repo.ensure_client(chat_id=-100501, name="Second client")
    for code in ("RUB", "USDT"):
        await repo.add_currency(second_client_id, code, 2)
    service = _service(pool)
    first_deal = await _deal(pool, client_id, suffix="concurrent-1")
    second_deal = await _deal(pool, second_client_id, suffix="concurrent-2")
    for deal_id in (first_deal, second_deal):
        await service.enqueue(
            EnqueueFulfillment(
                deal_id=deal_id,
                request_kind=FulfillmentRequestKind.SALE,
                qty=Decimal("70"),
            )
        )
    await _wallet_fact(pool, qty=Decimal("100"), key="concurrent-fact")

    results = await asyncio.gather(
        service.start_execution(_start(chat_id=-100500, suffix="concurrent-1")),
        service.start_execution(_start(chat_id=-100501, suffix="concurrent-2")),
        return_exceptions=True,
    )

    assert all(not isinstance(result, Exception) for result in results)
    assert [item.status for item in await service.list_active()].count(
        FulfillmentStatus.EXECUTING
    ) == 2


async def test_confirmed_firm_wallet_execution_updates_fact_position_and_queue_atomically(
    pool, client_id
) -> None:
    deal_id = await _deal(pool, client_id, suffix="complete")
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE deals SET status = 'awaiting_payment' WHERE id = $1",
            deal_id,
        )
    queue = _service(pool)
    await queue.enqueue(
        EnqueueFulfillment(
            deal_id=deal_id,
            request_kind=FulfillmentRequestKind.SALE,
            qty=Decimal("70"),
        )
    )
    await _wallet_fact(pool, qty=Decimal("100"), key="complete-fact")
    positions = FirmPositionAccountingService(partial(AsyncpgUnitOfWork, pool))
    await positions.record_opening(
        RecordOpening(
            currency="USDT",
            qty=Decimal("100"),
            rub_cost=Decimal("9000"),
            reason="test opening",
            idempotency_key="complete-position-opening",
        )
    )
    started = await queue.start_execution(_start(chat_id=-100500, suffix="complete"))
    settlement = DealSettlementService(
        partial(AsyncpgUnitOfWork, pool),
        fulfillment_queue_service=queue,
    )

    result = await settlement.settle(
        ConfirmedTransfer(
            watch_id=started.watch_id,
            tx_hash="complete-event",
            direction="IN",
            amount=Decimal("70"),
            token_symbol="USDT",
            confirmations=1,
            block_ts=datetime.now(UTC),
            from_address="TOurAddress",
            to_address="TClientAddress",
        )
    )

    assert result.status is SettlementReviewStatus.MATCHED
    completed = (await queue.list_active())
    assert completed == []
    position = await positions.position("USDT")
    assert position.qty == Decimal("30")
    assert position.rub_cost == Decimal("2700")
    summary = await queue.summary()
    assert summary.queued_qty == Decimal("0")
    assert summary.usdt_fact == Decimal("30")
    async with pool.acquire() as connection:
        item = await connection.fetchrow(
            """
            SELECT status, payment_event_id, position_move_id, wallet_source
            FROM usdt_fulfillment_queue WHERE deal_id = $1
            """,
            deal_id,
        )
    assert item["status"] == "completed"
    assert item["payment_event_id"] == result.event_id
    assert item["position_move_id"] is not None
    assert item["wallet_source"] == "firm_wallet"


async def test_confirmed_execution_ignores_stale_wallet_fact_without_overwriting_it(
    pool, client_id
) -> None:
    deal_id = await _deal(pool, client_id, suffix="stale-fact")
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE deals SET status = 'awaiting_payment' WHERE id = $1",
            deal_id,
        )
    queue = _service(pool)
    await queue.enqueue(
        EnqueueFulfillment(
            deal_id=deal_id,
            request_kind=FulfillmentRequestKind.SALE,
            qty=Decimal("70"),
        )
    )
    await _wallet_fact(pool, qty=Decimal("50"), key="stale-confirmed-fact")
    positions = FirmPositionAccountingService(partial(AsyncpgUnitOfWork, pool))
    await positions.record_opening(
        RecordOpening(
            currency="USDT",
            qty=Decimal("100"),
            rub_cost=Decimal("9000"),
            reason="stale wallet fact position",
            idempotency_key="stale-confirmed-position-opening",
        )
    )
    started = await queue.start_execution(
        _start(chat_id=-100500, suffix="stale-fact")
    )
    settlement = DealSettlementService(
        partial(AsyncpgUnitOfWork, pool),
        fulfillment_queue_service=queue,
    )

    result = await settlement.settle(
        ConfirmedTransfer(
            watch_id=started.watch_id,
            tx_hash="stale-confirmed-event",
            direction="IN",
            amount=Decimal("70"),
            token_symbol="USDT",
            confirmations=1,
            block_ts=datetime.now(UTC),
            from_address="TOurAddress",
            to_address="TClientAddress",
        )
    )

    assert result.status is SettlementReviewStatus.MATCHED
    assert await queue.list_active() == []
    assert (await positions.position("USDT")).qty == Decimal("30")
    assert (await queue.summary()).usdt_fact == Decimal("50")
    async with pool.acquire() as connection:
        assert await connection.fetchval(
            """
            SELECT COUNT(*) FROM firm_wallet_fact_snapshots
            WHERE source = 'payment_watch'
            """
        ) == 0


async def test_client_withdrawal_is_enqueued_by_otpr_and_debited_only_after_confirmation(
    pool, repo, client_id
) -> None:
    await repo.deposit(
        client_id=client_id,
        currency_code="USDT",
        amount=Decimal("40"),
        comment="withdrawable client balance",
        source="test",
        idempotency_key="client-withdrawal-opening",
    )
    await _wallet_fact(pool, qty=Decimal("50"), key="withdrawal-fact")
    positions = FirmPositionAccountingService(partial(AsyncpgUnitOfWork, pool))
    await positions.record_opening(
        RecordOpening(
            currency="USDT",
            qty=Decimal("50"),
            rub_cost=Decimal("4500"),
            reason="test withdrawal position",
            idempotency_key="withdrawal-position-opening",
        )
    )
    queue = _service(pool)

    started = await queue.start_execution(
        _start(chat_id=-100500, suffix="client-withdrawal")
    )

    assert started.item.request_kind is FulfillmentRequestKind.CLIENT_WITHDRAWAL
    assert started.item.qty == Decimal("40")
    assert await _client_usdt_balance(pool, client_id) == Decimal("40")
    settlement = DealSettlementService(
        partial(AsyncpgUnitOfWork, pool),
        fulfillment_queue_service=queue,
    )
    result = await settlement.settle(
        ConfirmedTransfer(
            watch_id=started.watch_id,
            tx_hash="client-withdrawal-event",
            direction="IN",
            amount=Decimal("40"),
            token_symbol="USDT",
            confirmations=1,
            block_ts=datetime.now(UTC),
            from_address="TOurAddress",
            to_address="TClientAddress",
        )
    )

    assert result.status is SettlementReviewStatus.MATCHED
    assert await _client_usdt_balance(pool, client_id) == Decimal("0")
    assert (await positions.position("USDT")).qty == Decimal("10")
    assert (await queue.summary()).usdt_fact == Decimal("10")
    async with pool.acquire() as connection:
        deal = await connection.fetchrow(
            "SELECT source_kind, status FROM deals WHERE id = $1",
            started.item.deal_id,
        )
    assert deal["source_kind"] == "fulfillment"
    assert deal["status"] == "done"


async def test_client_withdrawal_mismatch_waits_for_review_before_accounting(
    pool, repo, client_id
) -> None:
    await repo.deposit(
        client_id=client_id,
        currency_code="USDT",
        amount=Decimal("40"),
        comment="reviewed withdrawal balance",
        source="test",
        idempotency_key="reviewed-withdrawal-opening",
    )
    await _wallet_fact(pool, qty=Decimal("50"), key="reviewed-withdrawal-fact")
    positions = FirmPositionAccountingService(partial(AsyncpgUnitOfWork, pool))
    await positions.record_opening(
        RecordOpening(
            currency="USDT",
            qty=Decimal("50"),
            rub_cost=Decimal("4500"),
            reason="reviewed withdrawal position",
            idempotency_key="reviewed-withdrawal-position",
        )
    )
    queue = _service(pool)
    started = await queue.start_execution(
        _start(chat_id=-100500, suffix="reviewed-withdrawal")
    )
    settlement = DealSettlementService(
        partial(AsyncpgUnitOfWork, pool),
        fulfillment_queue_service=queue,
    )

    pending = await settlement.settle(
        ConfirmedTransfer(
            watch_id=started.watch_id,
            tx_hash="reviewed-withdrawal-event",
            direction="IN",
            amount=Decimal("30"),
            token_symbol="USDT",
            confirmations=1,
            block_ts=datetime.now(UTC),
            from_address="TOurAddress",
            to_address="TClientAddress",
        )
    )

    assert pending.status is SettlementReviewStatus.NEEDS_REVIEW
    assert await _client_usdt_balance(pool, client_id) == Decimal("40")
    assert (await positions.position("USDT")).qty == Decimal("50")
    assert (await queue.summary()).usdt_fact == Decimal("50")

    resolved = await settlement.resolve_review(
        ResolveSettlementCommand(
            settlement_id=pending.settlement_id,
            resolution=SettlementResolution.ACCEPT_ACTUAL,
            actor_user_id=await _actor_id(pool),
        )
    )

    assert resolved.status is SettlementReviewStatus.RESOLVED
    assert await _client_usdt_balance(pool, client_id) == Decimal("10")
    assert (await positions.position("USDT")).qty == Decimal("20")
    assert (await queue.summary()).usdt_fact == Decimal("20")
    assert await queue.list_active() == []


def _service(pool) -> FulfillmentQueueService:
    return FulfillmentQueueService(partial(AsyncpgUnitOfWork, pool))


async def _deal(pool, client_id: int, *, suffix: str) -> int:
    request_id = f"queue:{suffix}"
    await ExchangeRequestsRepo(pool).upsert_exchange_request_link(
        client_req_id=request_id,
        table_req_id=f"queue-table:{suffix}",
        table_in_cur="RUB",
        table_out_cur="USDT",
        table_in_amount=Decimal("9000"),
        table_out_amount=Decimal("100"),
        table_rate=Decimal("90"),
    )
    deal, _ = await DealRepository(pool).create_deal_idempotent(
        DealCreateCommand(
            deal_type="sale",
            city="екб",
            actor_user_id=None,
            client_id=client_id,
            source="tg_bot",
            source_kind="exchange",
            source_ref=request_id,
            exchange_client_req_id=request_id,
            body={
                "recv_code": "RUB",
                "recv_amount": "9000",
                "pay_code": "USDT",
                "pay_amount": "100",
                "rate": "90",
            },
        )
    )
    return deal.id


async def _actor_id(pool) -> int:
    async with pool.acquire() as connection:
        return int(
            await connection.fetchval(
                """
                INSERT INTO users (tg_user_id, display_name, role)
                VALUES (990002, 'Queue reviewer', 'manager')
                ON CONFLICT (tg_user_id) DO UPDATE
                SET display_name = EXCLUDED.display_name
                RETURNING id
                """
            )
        )


def _start(*, chat_id: int, suffix: str) -> StartFulfillmentExecution:
    return StartFulfillmentExecution(
        chat_id=chat_id,
        chat_name=f"Client {suffix}",
        reply_message_id=abs(hash(suffix)) % 100000,
        address=f"TClient{suffix}",
        our_address="TOurAddress",
        actor_user_id=None,
        mode="SINGLE",
        phase="MAIN",
        timeout_at=datetime.now(UTC),
    )


async def _wallet_fact(pool, *, qty: Decimal, key: str) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO firm_wallet_fact_snapshots(
                currency_code, actual_qty, observed_at, source, idempotency_key
            )
            VALUES ('USDT', $1, $2, 'manual', $3)
            """,
            qty,
            datetime.now(UTC),
            key,
        )


async def _client_usdt_balance(pool, client_id: int) -> Decimal:
    async with pool.acquire() as connection:
        value = await connection.fetchval(
            """
            SELECT t.balance_after
            FROM client_accounts a
            JOIN transactions t ON t.account_id = a.id
            WHERE a.client_id = $1 AND a.currency_code = 'USDT'
            ORDER BY t.id DESC
            LIMIT 1
            """,
            client_id,
        )
    return Decimal(str(value or 0))
