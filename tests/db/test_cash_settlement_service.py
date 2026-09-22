from __future__ import annotations

import json
from decimal import Decimal
from functools import partial

import pytest

from db_asyncpg.repositories.clients import ClientsRepo
from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.repositories.transactions import TransactionsRepo
from db_asyncpg.uow import AsyncpgUnitOfWork
from services.accounting.cash_settlement_models import CashSettlementCommand
from services.accounting.cash_settlement_service import (
    CashSettlementError,
    CashSettlementService,
)
from services.accounting.firm_position_service import FirmPositionAccountingService
from services.accounting.models import RecordOpening
from services.crm.deal_service import DealCreateCommand
from tests.db.conftest import balance_of

CLIENT_CHAT = -100500
CASH_CHAT = -700001
OTHER_CASH_CHAT = -700002


async def test_office_deposit_updates_cash_and_client_without_position(
    pool, repo, client_id
) -> None:
    await repo.add_currency(client_id, "USD", 2)
    cash_client_id = await _cash(pool, chat_id=CASH_CHAT, city="екб", code="USD")
    deal_id = await _deal(
        pool,
        client_id=client_id,
        request_id="Б-100001",
        kind="dep",
        amount=Decimal("1000"),
    )
    service = _service(pool)
    await service.mark_ready(request_id="Б-100001", actor_tg_user_id=77)

    result = await service.settle(_command("Б-100001", Decimal("1000")))

    assert result.deal_id == deal_id
    assert result.client_id == client_id
    assert result.client_chat_id == CLIENT_CHAT
    assert result.client_name == "Тестовый чат"
    assert result.position_move_id is None
    assert result.cash_balance == Decimal("1000")
    assert result.cash_precision == 2
    assert result.client_balance == Decimal("1000")
    assert result.client_precision == 2
    assert await balance_of(repo, client_id, "USD") == Decimal("1000")
    assert await balance_of(repo, cash_client_id, "USD") == Decimal("1000")
    async with pool.acquire() as connection:
        assert await connection.fetchval(
            "SELECT status FROM deals WHERE id = $1", deal_id
        ) == "done"
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM firm_position_moves"
        ) == 0
        events = await connection.fetch(
            """
            SELECT new_status, payload
            FROM deal_status_events
            WHERE deal_id = $1
            ORDER BY id
            """,
            deal_id,
        )
        assert [row["new_status"] for row in events] == [
            "new",
            "ready_for_cash_settlement",
            "done",
        ]
        assert json.loads(events[1]["payload"])["actorTgUserId"] == 77
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM tg_outbox WHERE kind = 'deal_status_changed'"
        ) == 2


async def test_office_withdrawal_updates_three_ledgers_at_average_entry(
    pool, repo, client_id
) -> None:
    await repo.add_currency(client_id, "USD", 2)
    await repo.deposit(
        client_id=client_id,
        currency_code="USD",
        amount=Decimal("125"),
        source="test",
        idempotency_key="withdrawal-client-opening",
    )
    cash_client_id = await _cash(pool, chat_id=CASH_CHAT, city="екб", code="USD")
    await repo.deposit(
        client_id=cash_client_id,
        currency_code="USD",
        amount=Decimal("125"),
        source="test",
        idempotency_key="withdrawal-cash-opening",
    )
    deal_id = await _deal(
        pool,
        client_id=client_id,
        request_id="Б-100002",
        kind="wd",
        amount=Decimal("125"),
    )
    positions = FirmPositionAccountingService(partial(AsyncpgUnitOfWork, pool))
    await positions.record_opening(
        RecordOpening(
            currency="USD_BL",
            qty=Decimal("125"),
            rub_cost=Decimal("11250"),
            reason="office withdrawal opening",
            idempotency_key="withdrawal-position-opening",
        )
    )
    service = CashSettlementService(
        partial(AsyncpgUnitOfWork, pool),
        position_service=positions,
    )
    await service.mark_ready(request_id="Б-100002", actor_tg_user_id=77)

    result = await service.settle(_command("Б-100002", Decimal("-125")))

    assert await balance_of(repo, client_id, "USD") == 0
    assert await balance_of(repo, cash_client_id, "USD") == 0
    assert (await positions.position("USD_BL")).qty == 0
    async with pool.acquire() as connection:
        move = await connection.fetchrow(
            "SELECT entry_rate, deal_id FROM firm_position_moves WHERE id = $1",
            result.position_move_id,
        )
    assert move["entry_rate"] == Decimal("90")
    assert move["deal_id"] == deal_id


async def test_duplicate_command_is_idempotent(pool, repo, client_id) -> None:
    await repo.add_currency(client_id, "USD", 2)
    cash_client_id = await _cash(pool, chat_id=CASH_CHAT, city="екб", code="USD")
    await _deal(
        pool,
        client_id=client_id,
        request_id="Б-100003",
        kind="dep",
        amount=Decimal("1000"),
    )
    service = _service(pool)
    await service.mark_ready(request_id="Б-100003", actor_tg_user_id=77)
    command = _command("Б-100003", Decimal("1000"))

    first = await service.settle(command)
    repeated = await service.settle(command)

    assert repeated.settlement_id == first.settlement_id
    assert repeated.repeated is True
    assert repeated.client_id == client_id
    assert repeated.client_chat_id == CLIENT_CHAT
    assert repeated.client_name == "Тестовый чат"
    assert repeated.cash_balance == Decimal("1000")
    assert repeated.client_balance == Decimal("1000")
    assert repeated.client_precision == 2
    assert await balance_of(repo, client_id, "USD") == Decimal("1000")
    assert await balance_of(repo, cash_client_id, "USD") == Decimal("1000")


async def test_internal_wallet_request_updates_only_city_cash(
    pool, repo, client_id
) -> None:
    await ClientsRepo(pool).set_client_group_by_chat_id(CLIENT_CHAT, "internal_wallet")
    cash_client_id = await _cash(pool, chat_id=CASH_CHAT, city="екб", code="RUB")
    await repo.deposit(
        client_id=cash_client_id,
        currency_code="RUB",
        amount=Decimal("1000"),
        source="test",
        idempotency_key="admin-cash-opening",
    )
    await _deal(
        pool,
        client_id=client_id,
        request_id="Б-100007",
        kind="wd",
        amount=Decimal("125"),
        currency="RUB",
    )
    service = _service(pool)
    await service.mark_ready(request_id="Б-100007", actor_tg_user_id=77)

    result = await service.settle(
        _command("Б-100007", Decimal("-125"), currency="RUB")
    )

    assert result.client_transaction_id is None
    assert result.client_balance is None
    assert result.client_precision is None
    assert await balance_of(repo, client_id, "RUB") == 0
    assert await balance_of(repo, cash_client_id, "RUB") == Decimal("875")
    async with pool.acquire() as connection:
        assert await connection.fetchval(
            "SELECT client_transaction_id FROM cash_settlements WHERE request_id = $1",
            "Б-100007",
        ) is None


async def test_wrong_city_or_request_does_not_write(pool, repo, client_id) -> None:
    await repo.add_currency(client_id, "USD", 2)
    cash_client_id = await _cash(pool, chat_id=CASH_CHAT, city="екб", code="USD")
    await _cash(pool, chat_id=OTHER_CASH_CHAT, city="члб", code="USD")
    await _deal(
        pool,
        client_id=client_id,
        request_id="Б-100004",
        kind="dep",
        amount=Decimal("1000"),
    )
    service = _service(pool)
    await service.mark_ready(request_id="Б-100004", actor_tg_user_id=77)

    with pytest.raises(CashSettlementError, match="активной кассе"):
        await service.settle(
            _command("Б-100004", Decimal("1000"), chat_id=OTHER_CASH_CHAT)
        )
    with pytest.raises(CashSettlementError, match="активной кассе"):
        await service.settle(_command("Б-999999", Decimal("1000")))

    assert await balance_of(repo, client_id, "USD") == 0
    assert await balance_of(repo, cash_client_id, "USD") == 0


async def test_failure_on_client_leg_rolls_back_cash_leg(
    pool, repo, client_id, monkeypatch
) -> None:
    await repo.add_currency(client_id, "USD", 2)
    cash_client_id = await _cash(pool, chat_id=CASH_CHAT, city="екб", code="USD")
    await _deal(
        pool,
        client_id=client_id,
        request_id="Б-100005",
        kind="dep",
        amount=Decimal("1000"),
    )
    service = _service(pool)
    await service.mark_ready(request_id="Б-100005", actor_tg_user_id=77)
    original = TransactionsRepo.deposit

    async def fail_client(self, **kwargs):
        if kwargs["client_id"] == client_id:
            raise RuntimeError("client ledger unavailable")
        return await original(self, **kwargs)

    monkeypatch.setattr(TransactionsRepo, "deposit", fail_client)
    with pytest.raises(RuntimeError, match="client ledger unavailable"):
        await service.settle(_command("Б-100005", Decimal("1000")))

    assert await balance_of(repo, client_id, "USD") == 0
    assert await balance_of(repo, cash_client_id, "USD") == 0
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT COUNT(*) FROM cash_settlements") == 0


async def test_failure_on_position_leg_rolls_back_both_balances(
    pool, repo, client_id, monkeypatch
) -> None:
    await repo.add_currency(client_id, "USD", 2)
    await repo.deposit(
        client_id=client_id,
        currency_code="USD",
        amount=Decimal("125"),
        source="test",
        idempotency_key="position-failure-client-opening",
    )
    cash_client_id = await _cash(pool, chat_id=CASH_CHAT, city="екб", code="USD")
    await repo.deposit(
        client_id=cash_client_id,
        currency_code="USD",
        amount=Decimal("125"),
        source="test",
        idempotency_key="position-failure-cash-opening",
    )
    await _deal(
        pool,
        client_id=client_id,
        request_id="Б-100006",
        kind="wd",
        amount=Decimal("125"),
    )
    service = _service(pool)
    await service.mark_ready(request_id="Б-100006", actor_tg_user_id=77)

    async def fail_position(*args, **kwargs):
        raise RuntimeError("position unavailable")

    monkeypatch.setattr(FirmPositionAccountingService, "record_sale", fail_position)
    with pytest.raises(RuntimeError, match="position unavailable"):
        await service.settle(_command("Б-100006", Decimal("-125")))

    assert await balance_of(repo, client_id, "USD") == Decimal("125")
    assert await balance_of(repo, cash_client_id, "USD") == Decimal("125")
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT COUNT(*) FROM cash_settlements") == 0


def _service(pool) -> CashSettlementService:
    return CashSettlementService(partial(AsyncpgUnitOfWork, pool))


def _command(
    request_id: str,
    signed_qty: Decimal,
    *,
    chat_id: int = CASH_CHAT,
    currency: str = "USD",
) -> CashSettlementCommand:
    return CashSettlementCommand(
        request_id=request_id,
        city_chat_id=chat_id,
        command_message_id=501,
        currency=currency,
        signed_qty=signed_qty,
        actor_tg_user_id=77,
        evidence={"photoFileId": "cash-proof"},
    )


async def _cash(pool, *, chat_id: int, city: str, code: str) -> int:
    clients = ClientsRepo(pool)
    client_id = await clients.ensure_client(chat_id, f"Касса {city}")
    await clients.add_currency(client_id, code, 2)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO cash_chat_registry(chat_id, client_id, city, location_name)
            VALUES ($1, $2, $3, $4)
            """,
            chat_id,
            client_id,
            city,
            f"Касса {city}",
        )
    return client_id


async def _deal(
    pool,
    *,
    client_id: int,
    request_id: str,
    kind: str,
    amount: Decimal,
    currency: str = "USD",
) -> int:
    deal, _ = await DealRepository(pool).create_deal_idempotent(
        DealCreateCommand(
            deal_type="deposit" if kind == "dep" else "withdrawal",
            city="екб",
            actor_user_id=None,
            client_id=client_id,
            source="tg_bot",
            source_kind="cash",
            source_ref=f"{CLIENT_CHAT}:{request_id}",
            body={
                "req_id": request_id,
                "request_kind": kind,
                "currency": currency,
                "amount": str(amount),
            },
        )
    )
    return deal.id
