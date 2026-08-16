from __future__ import annotations

from decimal import Decimal

from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.uow import AsyncpgUnitOfWork
from services.crm.deal_service import (
    DealCreateCommand,
    DealListFilter,
    DealStatusCommand,
)
from tests.db.conftest import balance_of, tx_rows


async def test_uncommitted_unit_of_work_rolls_back_all_repositories(
    pool, repo, exchange_requests_repo, client_id
) -> None:
    async with AsyncpgUnitOfWork(pool) as unit_of_work:
        await unit_of_work.transactions.deposit(
            client_id=client_id,
            currency_code="USDT",
            amount=Decimal("100"),
            source="uow_test",
            idempotency_key="uow:rollback:deposit",
        )
        await unit_of_work.exchange_requests.upsert_exchange_request_link(
            client_req_id="UOW-ROLLBACK",
            table_req_id="7001",
            status="active",
        )
        deal, created = await unit_of_work.deals.create_deal_idempotent(
            DealCreateCommand(
                deal_type="sale",
                city="екб",
                actor_user_id=None,
                client_id=client_id,
                source="tg_bot",
                source_kind="exchange",
                source_ref="uow:rollback",
                exchange_client_req_id="UOW-ROLLBACK",
            )
        )
        assert created is True
        await unit_of_work.deals.change_status(
            deal.id,
            DealStatusCommand(status="fixed", actor_user_id=None, payload={"rate": "90"}),
        )
        # No commit: __aexit__ must roll the complete aggregate back.

    assert await balance_of(repo, client_id, "USDT") == Decimal("0.00")
    assert await tx_rows(pool, client_id) == []
    assert (
        await exchange_requests_repo.get_exchange_request_link(client_req_id="UOW-ROLLBACK") is None
    )
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM deals") == 0
        assert await connection.fetchval("SELECT count(*) FROM tg_outbox") == 0


async def test_retry_after_rollback_commits_source_deal_event_and_outbox_once(
    pool, repo, exchange_requests_repo, client_id
) -> None:
    async def execute(*, commit: bool) -> None:
        async with AsyncpgUnitOfWork(pool) as unit_of_work:
            await unit_of_work.transactions.deposit(
                client_id=client_id,
                currency_code="USDT",
                amount=Decimal("100"),
                source="uow_test",
                idempotency_key="uow:retry:deposit",
            )
            await unit_of_work.exchange_requests.upsert_exchange_request_link(
                client_req_id="UOW-RETRY",
                table_req_id="7002",
                status="active",
            )
            deal, _ = await unit_of_work.deals.create_deal_idempotent(
                DealCreateCommand(
                    deal_type="sale",
                    city="екб",
                    actor_user_id=None,
                    client_id=client_id,
                    source="tg_bot",
                    source_kind="exchange",
                    source_ref="uow:retry",
                    exchange_client_req_id="UOW-RETRY",
                )
            )
            await unit_of_work.deals.change_status(
                deal.id,
                DealStatusCommand(status="fixed", actor_user_id=None, payload={"rate": "90"}),
            )
            if commit:
                await unit_of_work.commit()

    await execute(commit=False)
    await execute(commit=True)

    assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
    assert len(await tx_rows(pool, client_id)) == 1
    link = await exchange_requests_repo.get_exchange_request_link(client_req_id="UOW-RETRY")
    assert link is not None and link["status"] == "active"
    deals = await DealRepository(pool).list_deals(filters=DealListFilter())
    assert len(deals) == 1 and deals[0].status == "fixed"
    async with pool.acquire() as connection:
        outbox = await connection.fetch("SELECT kind, status FROM tg_outbox ORDER BY id")
    assert [(row["kind"], row["status"]) for row in outbox] == [("deal_status_changed", "pending")]
