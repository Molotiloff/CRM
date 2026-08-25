from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial

import pytest

from db_asyncpg.repositories.deal_workflow import DealWorkflowRepository
from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.uow import AsyncpgUnitOfWork
from services.accounting.fulfillment_models import (
    EnqueueFulfillment,
    FulfillmentRequestKind,
)
from services.accounting.fulfillment_service import FulfillmentQueueService
from services.crm.deal_events import DealEventBus
from services.crm.deal_service import (
    DealCreateCommand,
    DealService,
    DealStatusCommand,
    DealValidationError,
)
from services.crm.deal_status_policy import DealStatusPolicy

CLIENT_CHAT = -100500
REQUEST_CHAT = -777001


@pytest.mark.asyncio
async def test_exchange_status_workflow_queue_watch_and_tronscan(
    pool,
    repo,
    exchange_requests_repo,
    payment_watch_repo,
    client_id,
) -> None:
    await exchange_requests_repo.upsert_exchange_request_link(
        client_req_id="12345678",
        table_req_id="100001",
        client_chat_id=CLIENT_CHAT,
        client_message_id=101,
        request_chat_id=REQUEST_CHAT,
        request_message_id=202,
        request_text="Заявка 12345678",
        table_in_cur="RUB",
        table_out_cur="USDT",
        table_in_amount=Decimal("2500"),
        table_out_amount=Decimal("25"),
        table_rate=Decimal("100"),
    )
    deal_repository = DealRepository(pool)
    deal, _ = await deal_repository.create_deal_idempotent(
        DealCreateCommand(
            deal_type="purchase",
            city="екб",
            actor_user_id=None,
            client_id=client_id,
            source="tg_bot",
            source_kind="exchange",
            source_ref=f"{CLIENT_CHAT}:11",
            exchange_client_req_id="12345678",
            body={
                "client_req_id": "12345678",
                "recv_code": "RUB",
                "recv_amount": "2500",
                "pay_code": "USDT",
                "pay_amount": "25",
                "rate": "100",
            },
        )
    )
    queue = FulfillmentQueueService(partial(AsyncpgUnitOfWork, pool))
    await queue.enqueue(
        EnqueueFulfillment(
            deal_id=deal.id,
            request_kind=FulfillmentRequestKind.SALE,
            qty=Decimal("25"),
        )
    )

    service = DealService(
        deal_repository,
        DealEventBus(),
        DealStatusPolicy(DealWorkflowRepository(pool)),
    )
    await service.change_status(deal.id, _status("fixed"))
    checked = await service.change_status(deal.id, _status("balance_check"))

    assert checked.body.get("insufficient_usdt") is True
    assert checked.body.get("shortage_usdt") == "25.00000000"
    with pytest.raises(DealValidationError, match="insufficient"):
        await service.change_status(deal.id, _status("awaiting_payment"))

    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO firm_wallet_fact_snapshots(
                currency_code, actual_qty, observed_at, source, idempotency_key
            )
            VALUES ('USDT', 100, $1, 'manual', 'workflow-queue-fact')
            """,
            datetime.now(UTC),
        )
    rechecked = await service.change_status(deal.id, _status("balance_check"))
    assert rechecked.body.get("insufficient_usdt") is False

    watch_id = await payment_watch_repo.create_payment_watch(
        chat_id=CLIENT_CHAT,
        chat_name="Тестовый чат",
        reply_message_id=303,
        address="TClientAddress",
        our_address="TOurAddress",
        created_by_user_id=None,
        mode="SINGLE",
        phase="MAIN",
        status="WATCHING",
        timeout_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    awaiting = await service.change_status(
        deal.id,
        _status("awaiting_payment", {"paymentWatchId": watch_id}),
    )
    assert awaiting.body.get("payment_watch_id") == watch_id
    assert (await payment_watch_repo.get_payment_watch(watch_id=watch_id))["deal_id"] == deal.id

    payment_event_id = await payment_watch_repo.add_payment_watch_event(
        watch_id=watch_id,
        tx_hash="abc123",
        event_type="MAIN",
        direction="OUT",
        amount=Decimal("25"),
        token_symbol="USDT",
        confirmations=1,
        block_ts=datetime.now(UTC),
    )
    await payment_watch_repo.complete_payment_watch(watch_id=watch_id)
    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO deal_settlements (
                deal_id, payment_event_id, direction, currency_code,
                expected_qty, actual_qty, review_status, idempotency_key
            )
            VALUES ($1, $2, 'incoming', 'USDT', 25, 25, 'matched', $3)
            """,
            deal.id,
            payment_event_id,
            f"test:payment_event:{payment_event_id}",
        )
    completed = await service.change_status(deal.id, _status("done"))

    assert completed.tronscan_url is not None
    assert completed.tronscan_url.endswith("/abc123")
    assert completed.body.get("payment_tx_hash") == "abc123"
    assert [event.new_status for event in completed.status_events] == [
        "new",
        "fixed",
        "balance_check",
        "awaiting_payment",
        "done",
    ]
    async with pool.acquire() as con:
        outbox_count = await con.fetchval(
            "SELECT COUNT(*) FROM tg_outbox WHERE kind = 'deal_status_changed'"
        )
    assert outbox_count == 4


def _status(status: str, payload: dict | None = None) -> DealStatusCommand:
    return DealStatusCommand(
        status=status,
        actor_user_id=None,
        payload=payload or {},
    )
