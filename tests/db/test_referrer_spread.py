from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.repositories.transactions import TransactionsRepo
from domain import DomainStateError
from services.crm.deal_service import DealCreateCommand, DealStatusCommand, DealUpdateCommand
from tests.db.conftest import balance_of, tx_rows


async def _deal(pool, repo, client_id, *, status="new", kind="sale"):
    referrer = await repo.ensure_client(chat_id=-100501, name="КТ")
    await repo.add_currency(referrer, "RUB", 2)
    repository = DealRepository(pool)
    deal = await repository.create_deal(DealCreateCommand(
        deal_type=kind, city="екб", client_id=client_id, source="crm",
        source_kind="exchange", source_ref="spread:test", actor_user_id=None,
        body={"referrer_client_id": referrer, "referrer_spread_rub": "0.1",
              "recv_code": "RUB" if kind == "sale" else "USDT",
              "pay_code": "USDT" if kind == "sale" else "RUB",
              "recv_amount": "150000" if kind == "sale" else "1704.545",
              "pay_amount": "1704.545" if kind == "sale" else "150000"},
    ))
    if status != "new":
        await repository.change_status(deal.id, DealStatusCommand(status=status, actor_user_id=None))
    return repository, deal, referrer


@pytest.mark.parametrize("kind", ["sale", "purchase"])
async def test_spread_credit_is_atomic_and_concurrent_clicks_pay_once(pool, repo, client_id, kind):
    repository, deal, referrer = await _deal(pool, repo, client_id, kind=kind)
    results = await asyncio.gather(*[
        repository.pay_referrer_spread(deal.id, actor_user_id=1, actor_tg_user_id=42)
        for _ in range(6)
    ])
    assert sum(created for _, created in results) == 1
    assert await balance_of(repo, referrer, "RUB") == Decimal("170.45")
    rows = await tx_rows(pool, referrer, "RUB")
    assert len(rows) == 1
    saved = await repository.get_deal(deal.id)
    assert saved.body.get("referrer_spread_payment")["transaction_id"] == rows[0]["id"]
    assert saved.body.get("referrer_spread_payment")["amount_rub"] == "170.45"


async def test_spread_cancellation_returns_frozen_amount_once_even_if_balance_is_spent(pool, repo, client_id):
    repository, deal, referrer = await _deal(pool, repo, client_id)
    await repository.pay_referrer_spread(deal.id, actor_user_id=1, actor_tg_user_id=42)
    await repo.withdraw(client_id=referrer, currency_code="RUB", amount=Decimal("170"))
    await repository.change_status(deal.id, DealStatusCommand(status="canceled", actor_user_id=None))
    await repository.change_status(deal.id, DealStatusCommand(status="canceled", actor_user_id=None))
    assert await balance_of(repo, referrer, "RUB") == Decimal("-170")
    saved = await repository.get_deal(deal.id)
    assert saved.body.get("referrer_spread_payment")["status"] == "reversed"
    assert len(await tx_rows(pool, referrer, "RUB")) == 3
    with pytest.raises(DomainStateError):
        await repository.pay_referrer_spread(deal.id, actor_user_id=1, actor_tg_user_id=42)


async def test_spread_ledger_is_rolled_back_if_payment_metadata_fails(pool, repo, client_id, monkeypatch):
    repository, deal, referrer = await _deal(pool, repo, client_id)
    original = TransactionsRepo.deposit

    async def fail_after_deposit(self, **kwargs):
        await original(self, **kwargs)
        raise RuntimeError("failure after ledger")

    monkeypatch.setattr(TransactionsRepo, "deposit", fail_after_deposit)
    with pytest.raises(RuntimeError, match="failure after ledger"):
        await repository.pay_referrer_spread(deal.id, actor_user_id=1, actor_tg_user_id=42)
    assert await balance_of(repo, referrer, "RUB") == 0
    assert not await tx_rows(pool, referrer, "RUB")
    assert not (await repository.get_deal(deal.id)).body.get("referrer_spread_payment")


async def test_spread_cannot_be_paid_to_inactive_client(pool, repo, client_id):
    repository, deal, referrer = await _deal(pool, repo, client_id)
    async with pool.acquire() as con:
        await con.execute("UPDATE clients SET is_active=FALSE WHERE id=$1", referrer)
    with pytest.raises(DomainStateError):
        await repository.pay_referrer_spread(deal.id, actor_user_id=1, actor_tg_user_id=42)
    assert not await tx_rows(pool, referrer, "RUB")


@pytest.mark.parametrize("stale_field", ["amount", "recipient"])
async def test_spread_rejects_stale_confirmation(pool, repo, client_id, stale_field):
    repository, deal, referrer = await _deal(pool, repo, client_id)
    with pytest.raises(DomainStateError):
        await repository.pay_referrer_spread(
            deal.id, actor_user_id=1, actor_tg_user_id=42,
            expected_amount=Decimal("999") if stale_field == "amount" else Decimal("170.45"),
            expected_referrer_id=referrer + 1 if stale_field == "recipient" else referrer,
        )
    assert await balance_of(repo, referrer, "RUB") == 0
    assert not await tx_rows(pool, referrer, "RUB")


async def test_spread_can_be_paid_after_table_completion_but_amount_cannot_be_edited(pool, repo, client_id):
    repository, deal, referrer = await _deal(pool, repo, client_id, status="done")
    await repository.pay_referrer_spread(deal.id, actor_user_id=1, actor_tg_user_id=42)
    with pytest.raises(DomainStateError):
        await repository.update_deal(deal.id, DealUpdateCommand(body={"pay_amount": "999"}))
    assert await balance_of(repo, referrer, "RUB") == Decimal("170.45")


async def test_concurrent_cancellation_and_credit_leave_no_referrer_reward(pool, repo, client_id):
    repository, deal, referrer = await _deal(pool, repo, client_id)
    results = await asyncio.gather(
        repository.pay_referrer_spread(deal.id, actor_user_id=1, actor_tg_user_id=42),
        repository.change_status(deal.id, DealStatusCommand(status="canceled", actor_user_id=None)),
        return_exceptions=True,
    )
    assert all(not isinstance(result, Exception) or isinstance(result, DomainStateError) for result in results)
    assert (await repository.get_deal(deal.id)).status == "canceled"
    assert await balance_of(repo, referrer, "RUB") == 0
