import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest

from db_asyncpg.repositories.deliveries import DeliveryRepository
from db_asyncpg.repositories.transactions import TransactionsRepo
from domain import DomainStateError, DomainValidationError
from services.crm.deal_events import DealEventBus
from services.crm.deal_service import (
    DealCreateCommand,
    DealService,
    DealStatusCommand,
    DealUpdateCommand,
)
from services.crm.delivery_service import CreateDelivery, DeliveryService
from tests.db.conftest import balance_of
from tests.db.test_cash_settlement_service import CASH_CHAT, _cash
from tests.db.test_fulfillment_queue_service import _actor_id


async def setup_delivery(pool, repo, client_id, *, reward="250.50", cash_balance="10000"):
    actor = await _actor_id(pool)
    cash_id = await _cash(pool, chat_id=CASH_CHAT, city="екб", code="RUB")
    courier = await repo.ensure_client(-7441001, "Баланс курьера")
    await repo.add_currency(courier, "RUB", 2)
    await repo.add_currency(client_id, "RUB", 2)
    for target, value in [(cash_id, cash_balance), (client_id, "10000")]:
        await repo.deposit(client_id=target, currency_code="RUB", amount=Decimal(value))
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO managers(user_id,display_name) VALUES(7441001,'Курьер Telegram') ON CONFLICT(user_id) DO UPDATE SET display_name=EXCLUDED.display_name"
        )
    service = DeliveryService(DeliveryRepository(pool), DealEventBus())
    command = CreateDelivery(
        client_id=client_id,
        city="Екб",
        amount=Decimal("1000"),
        courier_tg_user_id=7441001,
        courier_client_id=courier,
        reward=Decimal(reward),
        actor_user_id=actor,
        idempotency_key="delivery-test",
        address="Тестовый адрес",
    )
    return service, command, await service.create(command), cash_id, courier


def transition(command, status="done"):
    return {
        "actor_user_id": command.actor_user_id,
        "status": status,
        "expected_amount": command.amount,
        "expected_reward": command.reward,
        "expected_courier_client_id": command.courier_client_id,
    }


async def test_delivery_creation_has_no_postings_and_remembers_explicit_wallet(
    pool, repo, client_id
):
    service, command, deal, cash_id, courier = await setup_delivery(pool, repo, client_id)
    repeated = await service.create(command)
    assert repeated.id == deal.id
    assert str(deal.status) == "new"
    assert await balance_of(repo, courier, "RUB") == 0
    assert await balance_of(repo, cash_id, "RUB") == 10000
    manager = next(item for item in await service.couriers() if item["id"] == "7441001")
    assert manager == {"id": "7441001", "name": "Курьер Telegram", "clientId": str(courier)}
    with pytest.raises(DomainStateError):
        await service.create(replace(command, reward=Decimal("251")))
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM delivery_postings") == 0
        assert await con.fetchval("SELECT count(*) FROM expenses") == 0


@pytest.mark.parametrize("start", [False, True])
async def test_delivery_completion_posts_all_legs_once_and_records_expense(
    pool, repo, client_id, start
):
    service, command, deal, cash_id, courier = await setup_delivery(pool, repo, client_id)
    if start:
        await service.transition(deal.id, **transition(command, "in_delivery"))
        assert await balance_of(repo, courier, "RUB") == 0
    results = await asyncio.gather(
        *(service.transition(deal.id, **transition(command)) for _ in range(3))
    )
    assert all(str(item.status) == "done" for item in results)
    assert await balance_of(repo, cash_id, "RUB") == 9000
    assert await balance_of(repo, client_id, "RUB") == 9000
    assert await balance_of(repo, courier, "RUB") == Decimal("250.50")
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM transactions WHERE source='delivery'") == 3
        assert await con.fetchval("SELECT count(*) FROM delivery_postings") == 1
        expense = await con.fetchrow("SELECT * FROM expenses")
        assert expense["amount"] == Decimal("250.50")
        assert expense["category"] == "Доставка"
        assert expense["comment"] == f"Курьер Telegram доставка по заявке {deal.deal_no}"
    with pytest.raises(DomainStateError):
        await service.transition(deal.id, **transition(command, "canceled"))


async def test_delivery_can_cancel_without_money_effects_but_cannot_complete_afterward(
    pool, repo, client_id
):
    service, command, deal, cash_id, courier = await setup_delivery(pool, repo, client_id)
    await service.transition(deal.id, **transition(command, "canceled"))
    await service.transition(deal.id, **transition(command, "canceled"))
    with pytest.raises(DomainStateError):
        await service.transition(deal.id, **transition(command))
    assert await balance_of(repo, courier, "RUB") == 0
    assert await balance_of(repo, cash_id, "RUB") == 10000


async def test_delivery_insufficient_cash_and_stale_parameters_do_not_post(pool, repo, client_id):
    service, command, deal, cash_id, courier = await setup_delivery(
        pool, repo, client_id, cash_balance="999"
    )
    with pytest.raises(DomainStateError, match="Недостаточно"):
        await service.transition(deal.id, **transition(command))
    with pytest.raises(DomainStateError, match="изменились"):
        await service.transition(
            deal.id, **{**transition(command), "expected_reward": Decimal("999")}
        )
    assert await balance_of(repo, cash_id, "RUB") == 999
    assert await balance_of(repo, courier, "RUB") == 0
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT status FROM deals WHERE id=$1", deal.id) == "new"
        assert await con.fetchval("SELECT count(*) FROM transactions WHERE source='delivery'") == 0


async def test_reward_failure_rolls_back_principal_cash_and_status(
    pool, repo, client_id, monkeypatch
):
    service, command, deal, cash_id, courier = await setup_delivery(pool, repo, client_id)
    original = TransactionsRepo.deposit

    async def fail_reward(self, **kwargs):
        if kwargs.get("source") == "delivery":
            raise RuntimeError("reward failed")
        return await original(self, **kwargs)

    monkeypatch.setattr(TransactionsRepo, "deposit", fail_reward)
    with pytest.raises(RuntimeError, match="reward failed"):
        await service.transition(deal.id, **transition(command))
    assert await balance_of(repo, cash_id, "RUB") == 10000
    assert await balance_of(repo, client_id, "RUB") == 10000
    assert await balance_of(repo, courier, "RUB") == 0
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT status FROM deals WHERE id=$1", deal.id) == "new"
        assert await con.fetchval("SELECT count(*) FROM expenses") == 0


async def test_delivery_invalid_courier_or_inactive_wallet_rejected(pool, repo, client_id):
    service, command, _, _, courier = await setup_delivery(pool, repo, client_id)
    with pytest.raises(DomainValidationError, match="менеджеров"):
        await service.create(
            replace(command, courier_tg_user_id=7441999, idempotency_key="invalid-courier")
        )
    async with pool.acquire() as con:
        await con.execute("UPDATE clients SET is_active=FALSE WHERE id=$1", courier)
    with pytest.raises(DomainValidationError, match="неактивен"):
        await service.create(replace(command, idempotency_key="inactive-wallet"))


async def test_free_delivery_has_no_reward_or_expense(pool, repo, client_id):
    service, command, deal, _, courier = await setup_delivery(pool, repo, client_id, reward="0")
    await service.transition(deal.id, **transition(command))
    assert await balance_of(repo, courier, "RUB") == 0
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM expenses") == 0
        assert await con.fetchval("SELECT count(*) FROM transactions WHERE source='delivery'") == 2


async def test_generic_deal_endpoints_cannot_bypass_delivery_accounting(pool, repo, client_id):
    from db_asyncpg.repositories.deals import DealRepository

    _, command, deal, _, _ = await setup_delivery(pool, repo, client_id)
    generic = DealService(DealRepository(pool), DealEventBus())
    with pytest.raises(DomainValidationError):
        await generic.create_deal(
            DealCreateCommand(deal_type="delivery", city="екб", actor_user_id=command.actor_user_id)
        )
    with pytest.raises(DomainValidationError):
        await generic.update_deal(deal.id, DealUpdateCommand(body={"courier_reward": "999"}))
    with pytest.raises(DomainValidationError):
        await generic.change_status(
            deal.id, DealStatusCommand(status="done", actor_user_id=command.actor_user_id)
        )
