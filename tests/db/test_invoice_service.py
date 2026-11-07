import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from api.read_repositories.dashboard import DashboardReadRepository
from db_asyncpg.adapters import ActCounterLedgerRepositoryAdapter
from db_asyncpg.repositories import ActCounterRepo, ClientsRepo, TransactionsRepo
from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.repositories.invoices import InvoiceRepository
from db_asyncpg.repositories.profit_accruals import ProfitAccrualsRepo
from domain import DomainStateError
from services.act_counter import ActCounterService
from services.crm.deal_events import DealEventBus
from services.crm.deal_service import (
    DealService,
    DealStatusCommand,
    DealUpdateCommand,
    DealValidationError,
)
from services.crm.invoice_service import CreateInvoice, InvoiceService
from tests.db.conftest import balance_of
from tests.db.test_fulfillment_queue_service import _actor_id, _wallet_fact

ACT_CHAT = -760040


def _act(pool):
    return ActCounterService(ActCounterLedgerRepositoryAdapter(ClientsRepo(pool), TransactionsRepo(pool), ActCounterRepo(pool)))


async def _act_amount(pool):
    return await _act(pool).get_current_amount(request_chat_id=ACT_CHAT, chat_name="ACT test")


async def _setup(pool, client_id, *, received="6000", paid="5900", act_balance="1000", request_chat_id=ACT_CHAT):
    actor = await _actor_id(pool)
    service = InvoiceService(InvoiceRepository(pool, request_chat_id=request_chat_id), DealEventBus())
    command = CreateInvoice(received=Decimal(received), paid=Decimal(paid), client_id=client_id, actor_user_id=actor, city="Екб", idempotency_key="invoice-create", comment="США → Дубай: инвойс 6000 USD")
    deal = await service.create(command)
    if act_balance is not None:
        await _act(pool).set_current_amount(request_chat_id=ACT_CHAT, chat_name="ACT test", amount=Decimal(act_balance), idempotency_key="invoice-act-opening")
        await ClientsRepo(pool).set_client_group_by_chat_id(ACT_CHAT, "internal_wallet")
    return service, command, deal


def _post(command):
    return {"actor_user_id": command.actor_user_id, "expected_received": command.received, "expected_paid": command.paid}


async def test_invoice_creation_is_idempotent_and_has_no_money_effects(pool, repo, client_id):
    service, command, deal = await _setup(pool, client_id)
    repeated = await service.create(command)
    assert deal.id == repeated.id
    assert str(deal.status) == "new"
    assert str(deal.city) == "екб"
    assert deal.body.get("profit_usdt") == "100"
    with pytest.raises(DomainStateError, match="другой заявки"):
        await service.create(replace(command, paid=Decimal("5800")))
    assert await balance_of(repo, client_id, "USDT") == 0
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM profit_usdt_accruals") == 0
        assert await con.fetchval("SELECT count(*) FROM firm_wallet_fact_snapshots") == 0
    assert await _act_amount(pool) == 1000


@pytest.mark.parametrize("received,paid,expected", [("6000", "5900", "100"), ("6000", "6100", "-100"), ("6000", "6000", "0"), ("1.00000001", "1", "0.00000001")])
async def test_post_invoice_changes_cash_once_and_never_client_or_position(pool, repo, client_id, received, paid, expected):
    service, command, deal = await _setup(pool, client_id, received=received, paid=paid)
    before = await DashboardReadRepository(pool).get_snapshot(business_date=datetime.now(UTC).date())
    posted = await service.post(deal.id, **_post(command), allow_loss=True)
    repeated = await service.post(deal.id, **_post(command), allow_loss=True)
    assert str(posted.status) == str(repeated.status) == "done"
    assert Decimal(posted.body.get("cash_after_usdt")) == Decimal("1000") + Decimal(expected)
    assert await balance_of(repo, client_id, "USDT") == 0
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM transactions WHERE client_id=$1", client_id) == 0
        assert await con.fetchval("SELECT count(*) FROM transactions WHERE source='invoice'") == (1 if Decimal(expected) != 0 else 0)
        assert await con.fetchval("SELECT count(*) FROM firm_position_moves") == 0
        assert await con.fetchval("SELECT count(*) FROM profit_usdt_accruals WHERE capitalization_status='cash_only'") == 1
        assert await con.fetchval("SELECT sum(qty) FROM profit_usdt_accruals") == Decimal(expected)
        assert await con.fetchval("SELECT count(*) FROM firm_wallet_fact_snapshots") == 0
        assert await con.fetchval("SELECT count(*) FROM deal_status_events WHERE deal_id=$1 AND new_status='done'", deal.id) == 1
        # Cash-only invoice profit carries forward without re-entering firm position.
        await con.execute("UPDATE deals SET deal_at=deal_at-7 WHERE id=$1", deal.id)
    pending = await ProfitAccrualsRepo(pool).list_pending_before(datetime.now(UTC)+timedelta(days=1))
    assert pending == []
    after = await DashboardReadRepository(pool).get_snapshot(business_date=datetime.now(UTC).date())
    old = next(item for item in before.currencies if item.code == "USDT")
    new = next(item for item in after.currencies if item.code == "USDT")
    assert new.deal_profit_qty-old.deal_profit_qty == Decimal(expected)
    assert new.client_qty == old.client_qty
    assert new.observed_qty is old.observed_qty is None
    assert await _act_amount(pool) == Decimal("1000") + Decimal(expected)
    assert posted.body.get("cash_basis") == "act"
    assert posted.body.get("act_chat_id") == ACT_CHAT


async def test_loss_requires_confirmation_before_any_cash_or_profit_write(pool, client_id):
    service, command, deal = await _setup(pool, client_id, paid="6100")
    with pytest.raises(DomainStateError, match="Подтвердите"):
        await service.post(deal.id, **_post(command))
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM profit_usdt_accruals") == 0
        assert await con.fetchval("SELECT count(*) FROM firm_wallet_fact_snapshots") == 0
        assert await con.fetchval("SELECT count(*) FROM transactions WHERE source='invoice'") == 0
        assert await con.fetchval("SELECT status FROM deals WHERE id=$1", deal.id) == "new"


@pytest.mark.parametrize("act_balance,paid,message", [(None, "5900", "/акт"), ("0", "6100", "Недостаточно USDT")])
async def test_invoice_uses_act_balance_and_cannot_overdraw_it(pool, client_id, act_balance, paid, message):
    service, command, deal = await _setup(pool, client_id, act_balance=act_balance, paid=paid)
    with pytest.raises(DomainStateError, match=message):
        await service.post(deal.id, **_post(command), allow_loss=True)


async def test_cancel_unposted_invoice_has_no_cash_effect_and_blocks_posting(pool, client_id):
    service, command, deal = await _setup(pool, client_id)
    canceled = await service.cancel(deal.id, actor_user_id=command.actor_user_id, comment="Не оплачен")
    assert str(canceled.status) == "canceled"
    await service.cancel(deal.id, actor_user_id=command.actor_user_id)
    with pytest.raises(DomainStateError):
        await service.post(deal.id, **_post(command))
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM profit_usdt_accruals") == 0
        assert await con.fetchval("SELECT count(*) FROM firm_wallet_fact_snapshots") == 0
        assert await con.fetchval("SELECT count(*) FROM transactions WHERE source='invoice'") == 0


async def test_posted_invoice_and_generic_endpoints_cannot_bypass_accounting(pool, client_id):
    service, command, deal = await _setup(pool, client_id)
    generic = DealService(DealRepository(pool), DealEventBus())
    with pytest.raises(DealValidationError):
        await generic.change_status(deal.id, DealStatusCommand(status="done", actor_user_id=command.actor_user_id))
    with pytest.raises(DealValidationError):
        await generic.update_deal(deal.id, DealUpdateCommand(body={"paid_usdt": "1"}))
    with pytest.raises(DomainStateError, match="изменились"):
        await service.post(deal.id, **{**_post(command), "expected_paid": Decimal("5800")})
    await service.post(deal.id, **_post(command))
    with pytest.raises(DomainStateError, match="нельзя отменить"):
        await service.cancel(deal.id, actor_user_id=command.actor_user_id)


async def test_post_failure_rolls_back_cash_profit_and_status_together(pool, client_id, monkeypatch):
    service, command, deal = await _setup(pool, client_id)
    async def fail(*args, **kwargs):
        raise RuntimeError("status storage unavailable")
    monkeypatch.setattr(DealRepository, "change_status", fail)
    with pytest.raises(RuntimeError, match="storage"):
        await service.post(deal.id, **_post(command))
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM profit_usdt_accruals") == 0
        assert await con.fetchval("SELECT count(*) FROM firm_wallet_fact_snapshots") == 0
        assert await con.fetchval("SELECT count(*) FROM transactions WHERE source='invoice'") == 0
        assert await con.fetchval("SELECT status FROM deals WHERE id=$1", deal.id) == "new"
    assert await _act_amount(pool) == 1000


async def test_concurrent_invoice_posts_do_not_duplicate_profit(pool, client_id):
    service, command, deal = await _setup(pool, client_id)
    results = await asyncio.gather(*(service.post(deal.id, **_post(command)) for _ in range(3)), return_exceptions=True)
    assert not any(isinstance(result, Exception) for result in results), results
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM profit_usdt_accruals") == 1
        assert await con.fetchval("SELECT count(*) FROM firm_wallet_fact_snapshots") == 0
        assert await con.fetchval("SELECT count(*) FROM transactions WHERE source='invoice'") == 1
    assert await _act_amount(pool) == 1100


async def test_invoice_ignores_physical_snapshot_and_preserves_existing_act_name(pool, client_id):
    service, command, deal = await _setup(pool, client_id)
    await _wallet_fact(pool, qty=Decimal("9999"), key="unrelated-physical-wallet")
    await service.post(deal.id, **_post(command))
    assert await _act_amount(pool) == 1100
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT actual_qty FROM firm_wallet_fact_snapshots") == 9999
        assert await con.fetchval("SELECT name FROM clients WHERE chat_id=$1", ACT_CHAT) == "ACT test"


async def test_invoice_requires_configured_request_chat(pool, client_id):
    service, command, deal = await _setup(pool, client_id, request_chat_id=None)
    with pytest.raises(DomainStateError, match="REQUEST_CHAT_ID"):
        await service.post(deal.id, **_post(command))
    assert await _act_amount(pool) == 1000


async def test_two_invoices_serialize_act_changes_without_lost_updates(pool, client_id):
    service, command, first = await _setup(pool, client_id)
    second_command = replace(command, paid=Decimal("5800"), idempotency_key="second-invoice")
    second = await service.create(second_command)
    results = await asyncio.gather(service.post(first.id, **_post(command)), service.post(second.id, **_post(second_command)), return_exceptions=True)
    assert not any(isinstance(result, Exception) for result in results), results
    assert await _act_amount(pool) == 1300


async def test_act_is_not_counted_as_client_money_after_invoice(pool, client_id):
    service, command, deal = await _setup(pool, client_id)
    await ClientsRepo(pool).set_client_group_by_chat_id(ACT_CHAT, "ordinary")
    await service.post(deal.id, **_post(command))
    snapshot = await DashboardReadRepository(pool).get_snapshot(business_date=datetime.now(UTC).date())
    usdt = next(item for item in snapshot.currencies if item.code == "USDT")
    assert usdt.client_qty == 0
    assert usdt.deal_profit_qty == 100
