import asyncio
from decimal import Decimal

import pytest

from services.accounting.cash_settlement_service import CashSettlementError
from tests.db.conftest import balance_of
from tests.db.test_cash_settlement_service import CASH_CHAT, _cash, _command, _deal, _service


@pytest.mark.parametrize("source", ["crm", "tg_bot"])
@pytest.mark.parametrize("kind", ["dep", "wd"])
@pytest.mark.parametrize("ready", [False, True])
async def test_cash_command_posts_new_and_legacy_ready_requests(
    pool, repo, client_id, source, kind, ready
):
    await repo.add_currency(client_id, "RUB", 2)
    cash_id = await _cash(pool, chat_id=CASH_CHAT, city="екб", code="RUB")
    if kind == "wd":
        for target in (client_id, cash_id):
            await repo.deposit(client_id=target, currency_code="RUB", amount=Decimal("1000"))
    deal_id = await _deal(
        pool,
        client_id=client_id,
        request_id="Б-777001",
        kind=kind,
        amount=Decimal("1000"),
        currency="RUB",
        source=source,
    )
    service = _service(pool)
    if ready:
        await service.mark_ready(request_id="Б-777001", actor_tg_user_id=77)
    # Actual amount can differ from the original request, as in the existing workflow.
    result = await service.settle(
        _command("Б-777001", Decimal("250") if kind == "dep" else Decimal("-250"), currency="RUB")
    )
    assert result.actual_qty == 250
    expected = Decimal("250") if kind == "dep" else Decimal("750")
    assert await balance_of(repo, cash_id, "RUB") == expected
    assert await balance_of(repo, client_id, "RUB") == expected
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT status FROM deals WHERE id=$1", deal_id) == "done"
        assert (
            await con.fetchval("SELECT body->>'settled_qty' FROM deals WHERE id=$1", deal_id)
            == "250"
        )
        statuses = await con.fetch(
            "SELECT new_status FROM deal_status_events WHERE deal_id=$1 ORDER BY id", deal_id
        )
        assert [row["new_status"] for row in statuses] == (
            ["new", "ready_for_cash_settlement", "done"] if ready else ["new", "done"]
        )


@pytest.mark.parametrize("status", ["done", "canceled", "in_delivery"])
async def test_non_pending_cash_request_cannot_post(pool, repo, client_id, status):
    cash_id = await _cash(pool, chat_id=CASH_CHAT, city="екб", code="USD")
    await repo.add_currency(client_id, "USD", 2)
    deal_id = await _deal(
        pool, client_id=client_id, request_id="Б-777002", kind="dep", amount=Decimal("1000")
    )
    async with pool.acquire() as con:
        await con.execute("UPDATE deals SET status=$2 WHERE id=$1", deal_id, status)
    with pytest.raises(CashSettlementError, match="проведение недоступно"):
        await _service(pool).settle(_command("Б-777002", Decimal("1000")))
    assert await balance_of(repo, client_id, "USD") == 0
    assert await balance_of(repo, cash_id, "USD") == 0


@pytest.mark.parametrize(
    "kind,quantity,currency", [("dep", "-100", "USD"), ("wd", "100", "USD"), ("dep", "100", "EUR")]
)
async def test_direct_cash_command_keeps_sign_and_currency_validation(
    pool, repo, client_id, kind, quantity, currency
):
    cash_id = await _cash(pool, chat_id=CASH_CHAT, city="екб", code="USD")
    await repo.add_currency(client_id, "USD", 2)
    await _deal(pool, client_id=client_id, request_id="Б-777003", kind=kind, amount=Decimal("1000"))
    with pytest.raises(CashSettlementError):
        await _service(pool).settle(_command("Б-777003", Decimal(quantity), currency=currency))
    assert await balance_of(repo, client_id, "USD") == 0
    assert await balance_of(repo, cash_id, "USD") == 0


async def test_simultaneous_direct_settlements_post_only_once(pool, repo, client_id):
    cash_id = await _cash(pool, chat_id=CASH_CHAT, city="екб", code="USD")
    await repo.add_currency(client_id, "USD", 2)
    await _deal(
        pool, client_id=client_id, request_id="Б-777004", kind="dep", amount=Decimal("1000")
    )
    service = _service(pool)
    command = _command("Б-777004", Decimal("1000"))
    results = await asyncio.gather(
        *(service.settle(command) for _ in range(3)), return_exceptions=True
    )
    assert not any(isinstance(result, Exception) for result in results), results
    assert sum(not result.repeated for result in results) == 1
    assert await balance_of(repo, client_id, "USD") == 1000
    assert await balance_of(repo, cash_id, "USD") == 1000
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM cash_settlements") == 1
        assert (
            await con.fetchval("SELECT count(*) FROM transactions WHERE source='cash_settlement'")
            == 2
        )
