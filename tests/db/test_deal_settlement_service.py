from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial

import pytest

from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.repositories.exchange_requests import ExchangeRequestsRepo
from db_asyncpg.repositories.settlements import BlockchainEventAlreadyClaimedError
from db_asyncpg.uow import AsyncpgUnitOfWork
from domain import SettlementReviewStatus
from domain.accounting_flows import SettlementResolution
from services.crm.deal_service import DealCreateCommand
from services.payment_watch.settlement_models import (
    ConfirmedTransfer,
    ReplacementExchange,
    ResolveSettlementCommand,
)
from services.payment_watch.settlement_service import DealSettlementService
from tests.db.conftest import balance_of


@pytest.mark.parametrize(
    ("actual", "status", "balance", "deal_status"),
    [
        (Decimal("100"), SettlementReviewStatus.MATCHED, Decimal("100"), "done"),
        (Decimal("90"), SettlementReviewStatus.NEEDS_REVIEW, Decimal("90"), "awaiting_payment"),
        (Decimal("110"), SettlementReviewStatus.NEEDS_REVIEW, Decimal("110"), "awaiting_payment"),
    ],
)
async def test_settlement_reconciles_only_actual_delta(
    pool,
    repo,
    payment_watch_repo,
    client_id,
    actual: Decimal,
    status: SettlementReviewStatus,
    balance: Decimal,
    deal_status: str,
) -> None:
    await repo.deposit(
        client_id=client_id,
        currency_code="USDT",
        amount=Decimal("100"),
        source="exchange",
        idempotency_key="contract:recv",
    )
    deal_id, watch_id = await _deal_and_watch(pool, payment_watch_repo, client_id)

    result = await _service(pool).settle(_transfer(watch_id, actual, "tx-main"))

    assert result.status is status
    assert result.delta == actual - Decimal("100")
    assert await balance_of(repo, client_id, "USDT") == balance
    async with pool.acquire() as con:
        deal = await con.fetchrow("SELECT status FROM deals WHERE id = $1", deal_id)
        event_count = await con.fetchval("SELECT COUNT(*) FROM payment_watch_events")
        settlement_count = await con.fetchval("SELECT COUNT(*) FROM deal_settlements")
        review_outbox = await con.fetchval(
            "SELECT COUNT(*) FROM tg_outbox WHERE kind = 'settlement_needs_review'"
        )
    assert deal["status"] == deal_status
    assert event_count == 1
    assert settlement_count == 1
    assert review_outbox == (1 if status is SettlementReviewStatus.NEEDS_REVIEW else 0)


async def test_duplicate_event_is_idempotent(
    pool, repo, payment_watch_repo, client_id
) -> None:
    await repo.deposit(
        client_id=client_id,
        currency_code="USDT",
        amount=Decimal("100"),
        source="exchange",
        idempotency_key="contract:recv",
    )
    _, watch_id = await _deal_and_watch(pool, payment_watch_repo, client_id)
    service = _service(pool)
    transfer = _transfer(watch_id, Decimal("90"), "tx-duplicate")

    first = await service.settle(transfer)
    second = await service.settle(transfer)

    assert first.settlement_id == second.settlement_id
    assert second.created is False
    assert await balance_of(repo, client_id, "USDT") == Decimal("90")


async def test_one_blockchain_event_cannot_settle_two_watches(
    pool, repo, payment_watch_repo, client_id
) -> None:
    await repo.deposit(
        client_id=client_id,
        currency_code="USDT",
        amount=Decimal("200"),
        source="exchange",
        idempotency_key="contract:recv",
    )
    _, first_watch = await _deal_and_watch(pool, payment_watch_repo, client_id, suffix="one")
    _, second_watch = await _deal_and_watch(pool, payment_watch_repo, client_id, suffix="two")
    service = _service(pool)
    await service.settle(_transfer(first_watch, Decimal("100"), "tx-claimed"))

    with pytest.raises(BlockchainEventAlreadyClaimedError):
        await service.settle(_transfer(second_watch, Decimal("100"), "tx-claimed"))

    second = await payment_watch_repo.get_payment_watch(watch_id=second_watch)
    assert second["status"] == "WATCHING"


async def test_ledger_failure_rolls_back_event_and_settlement(
    pool, payment_watch_repo, client_id
) -> None:
    deal_id, watch_id = await _deal_and_watch(
        pool,
        payment_watch_repo,
        client_id,
        recv_code="EUR",
    )

    with pytest.raises(KeyError, match="account not found"):
        await _service(pool).settle(_transfer(watch_id, Decimal("90"), "tx-rollback", "EUR"))

    async with pool.acquire() as con:
        assert await con.fetchval("SELECT COUNT(*) FROM payment_watch_events") == 0
        assert await con.fetchval("SELECT COUNT(*) FROM deal_settlements") == 0
        assert await con.fetchval("SELECT status FROM deals WHERE id = $1", deal_id) == "awaiting_payment"


async def test_accept_actual_completes_review_without_another_ledger_write(
    pool, repo, payment_watch_repo, client_id
) -> None:
    settlement_id, deal_id = await _pending_review(
        pool, repo, payment_watch_repo, client_id, suffix="accept"
    )
    before_count = await _transaction_count(pool)
    command = ResolveSettlementCommand(
        settlement_id=settlement_id,
        resolution=SettlementResolution.ACCEPT_ACTUAL,
        actor_user_id=await _actor_id(pool),
        comment="accepted actual transfer",
    )

    first = await _service(pool).resolve_review(command)
    retry = await _service(pool).resolve_review(command)

    assert first.status is SettlementReviewStatus.RESOLVED
    assert retry.settlement_id == first.settlement_id
    assert await _transaction_count(pool) == before_count
    assert await balance_of(repo, client_id, "USDT") == Decimal("90")
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT status FROM deals WHERE id = $1", deal_id) == "done"


async def test_amend_unposted_updates_contract_without_reposting_ledger(
    pool, repo, payment_watch_repo, client_id
) -> None:
    settlement_id, deal_id = await _pending_review(
        pool, repo, payment_watch_repo, client_id, suffix="amend"
    )
    before_count = await _transaction_count(pool)

    await _service(pool).resolve_review(
        ResolveSettlementCommand(
            settlement_id=settlement_id,
            resolution=SettlementResolution.AMEND_UNPOSTED,
            actor_user_id=await _actor_id(pool),
        )
    )

    assert await _transaction_count(pool) == before_count
    async with pool.acquire() as con:
        row = await con.fetchrow(
            """
            SELECT d.status, d.body->>'recv_amount' AS body_amount,
                   erl.table_in_amount
            FROM deals d
            JOIN exchange_request_links erl
              ON erl.client_req_id = d.exchange_client_req_id
            WHERE d.id = $1
            """,
            deal_id,
        )
    assert row["status"] == "done"
    assert Decimal(row["body_amount"]) == Decimal("90")
    assert Decimal(str(row["table_in_amount"])) == Decimal("90")


async def test_cancel_and_recreate_reverses_effective_legs_then_applies_new_terms(
    pool, repo, payment_watch_repo, client_id
) -> None:
    settlement_id, old_deal_id = await _pending_review(
        pool, repo, payment_watch_repo, client_id, suffix="recreate"
    )

    await _service(pool).resolve_review(
        ResolveSettlementCommand(
            settlement_id=settlement_id,
            resolution=SettlementResolution.CANCEL_AND_RECREATE,
            actor_user_id=await _actor_id(pool),
            comment="replacement terms agreed",
            replacement=ReplacementExchange(
                request_id="replacement-1",
                table_request_id="replacement-table-1",
                recv_code="USDT",
                recv_amount=Decimal("80"),
                pay_code="RUB",
                pay_amount=Decimal("7200"),
                rate=Decimal("90"),
            ),
        )
    )

    assert await balance_of(repo, client_id, "USDT") == Decimal("80")
    assert await balance_of(repo, client_id, "RUB") == Decimal("-7200")
    async with pool.acquire() as con:
        old_status = await con.fetchval("SELECT status FROM deals WHERE id = $1", old_deal_id)
        replacement = await con.fetchrow(
            """
            SELECT d.status, d.body->>'replaces_deal_id' AS replaces_deal_id,
                   erl.status AS request_status
            FROM deals d
            JOIN exchange_request_links erl
              ON erl.client_req_id = d.exchange_client_req_id
            WHERE d.exchange_client_req_id = 'replacement-1'
            """
        )
        old_request_status = await con.fetchval(
            "SELECT status FROM exchange_request_links WHERE client_req_id = 'req:recreate'"
        )
    assert old_status == "canceled"
    assert old_request_status == "cancelled"
    assert replacement["status"] == "new"
    assert replacement["request_status"] == "active"
    assert replacement["replaces_deal_id"] == str(old_deal_id)


async def test_recreate_failure_rolls_back_reversals_statuses_and_new_source(
    pool, repo, payment_watch_repo, client_id, monkeypatch
) -> None:
    settlement_id, old_deal_id = await _pending_review(
        pool, repo, payment_watch_repo, client_id, suffix="recreate-rollback"
    )

    async def fail_replacement(*args, **kwargs) -> None:
        raise RuntimeError("replacement deal unavailable")

    monkeypatch.setattr(DealRepository, "create_deal_idempotent", fail_replacement)
    with pytest.raises(RuntimeError, match="replacement deal unavailable"):
        await _service(pool).resolve_review(
            ResolveSettlementCommand(
                settlement_id=settlement_id,
                resolution=SettlementResolution.CANCEL_AND_RECREATE,
                actor_user_id=await _actor_id(pool),
                replacement=ReplacementExchange(
                    request_id="replacement-rollback",
                    table_request_id="replacement-table-rollback",
                    recv_code="USDT",
                    recv_amount=Decimal("80"),
                    pay_code="RUB",
                    pay_amount=Decimal("7200"),
                    rate=Decimal("90"),
                ),
            )
        )

    assert await balance_of(repo, client_id, "USDT") == Decimal("90")
    assert await balance_of(repo, client_id, "RUB") == Decimal("-9000")
    async with pool.acquire() as con:
        settlement = await con.fetchrow(
            "SELECT review_status, resolution FROM deal_settlements WHERE id = $1",
            settlement_id,
        )
        old_status = await con.fetchval("SELECT status FROM deals WHERE id = $1", old_deal_id)
        old_request_status = await con.fetchval(
            """
            SELECT status FROM exchange_request_links
            WHERE client_req_id = 'req:recreate-rollback'
            """
        )
        replacement_count = await con.fetchval(
            """
            SELECT COUNT(*) FROM exchange_request_links
            WHERE client_req_id = 'replacement-rollback'
            """
        )
    assert settlement["review_status"] == "needs_review"
    assert settlement["resolution"] is None
    assert old_status == "awaiting_payment"
    assert old_request_status == "active"
    assert replacement_count == 0


def _service(pool) -> DealSettlementService:
    return DealSettlementService(partial(AsyncpgUnitOfWork, pool))


async def _pending_review(
    pool,
    repo,
    payment_watch_repo,
    client_id: int,
    *,
    suffix: str,
) -> tuple[int, int]:
    await repo.deposit(
        client_id=client_id,
        currency_code="USDT",
        amount=Decimal("100"),
        source="exchange",
        idempotency_key=f"contract:{suffix}:recv",
    )
    await repo.withdraw(
        client_id=client_id,
        currency_code="RUB",
        amount=Decimal("9000"),
        source="exchange",
        idempotency_key=f"contract:{suffix}:pay",
    )
    deal_id, watch_id = await _deal_and_watch(
        pool, payment_watch_repo, client_id, suffix=suffix
    )
    result = await _service(pool).settle(
        _transfer(watch_id, Decimal("90"), f"tx-{suffix}")
    )
    return result.settlement_id, deal_id


async def _actor_id(pool) -> int:
    async with pool.acquire() as con:
        return int(
            await con.fetchval(
                """
                INSERT INTO users (tg_user_id, display_name, role)
                VALUES (990001, 'Settlement reviewer', 'manager')
                ON CONFLICT (tg_user_id) DO UPDATE
                SET display_name = EXCLUDED.display_name
                RETURNING id
                """
            )
        )


async def _transaction_count(pool) -> int:
    async with pool.acquire() as con:
        return int(await con.fetchval("SELECT COUNT(*) FROM transactions"))


def _transfer(
    watch_id: int,
    amount: Decimal,
    tx_hash: str,
    token_symbol: str = "USDT",
) -> ConfirmedTransfer:
    return ConfirmedTransfer(
        watch_id=watch_id,
        tx_hash=tx_hash,
        direction="IN",
        amount=amount,
        token_symbol=token_symbol,
        confirmations=1,
        block_ts=datetime.now(UTC),
        from_address="TOurAddress",
        to_address="TClientAddress",
    )


async def _deal_and_watch(
    pool,
    payment_watch_repo,
    client_id: int,
    *,
    suffix: str = "default",
    recv_code: str = "USDT",
) -> tuple[int, int]:
    request_id = f"req:{suffix}"
    await ExchangeRequestsRepo(pool).upsert_exchange_request_link(
        client_req_id=request_id,
        table_req_id=str(abs(hash(suffix)) % 100000 + 1),
        table_in_cur=recv_code,
        table_out_cur="RUB",
        table_in_amount=Decimal("100"),
        table_out_amount=Decimal("9000"),
        table_rate=Decimal("90"),
        status="active",
    )
    deal, _ = await DealRepository(pool).create_deal_idempotent(
        DealCreateCommand(
            deal_type="purchase",
            city="екб",
            actor_user_id=None,
            client_id=client_id,
            source="tg_bot",
            source_kind="exchange",
            source_ref=f"settlement:{suffix}",
            exchange_client_req_id=request_id,
            body={
                "recv_code": recv_code,
                "recv_amount": "100",
                "pay_code": "RUB",
                "pay_amount": "9000",
                "rate": "90",
            },
        )
    )
    watch_id = await payment_watch_repo.create_payment_watch(
        chat_id=-100500,
        chat_name="Client",
        reply_message_id=hash(suffix) % 100000,
        address="TClientAddress",
        our_address="TOurAddress",
        created_by_user_id=None,
        mode="SINGLE",
        phase="MAIN",
        status="WATCHING",
        timeout_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    async with pool.acquire() as con:
        await con.execute(
            "UPDATE deals SET status = 'awaiting_payment' WHERE id = $1",
            deal.id,
        )
        await con.execute(
            "UPDATE payment_watches SET deal_id = $1 WHERE id = $2",
            deal.id,
            watch_id,
        )
    return deal.id, watch_id
