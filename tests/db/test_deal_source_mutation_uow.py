from __future__ import annotations

from decimal import Decimal
from functools import partial

import pytest

from db_asyncpg.adapters import (
    ClientWalletScheduleContextAdapter,
    DealSourceRecordReaderAdapter,
    DealSourceRepositoryAdapter,
)
from db_asyncpg.repositories import ClientsRepo, ExchangeRequestsRepo, RequestScheduleRepo
from db_asyncpg.repositories.deal_workflow import DealWorkflowRepository
from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.uow import AsyncpgUnitOfWork
from services.cash_requests.edit_cash_request import EditCashRequest
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from services.crm.cash_deal_source_adapter import CashDealSourceAdapter
from services.crm.deal_events import DealEventBus
from services.crm.deal_service import DealCreateCommand, DealService
from services.crm.deal_source_adapter import DealSourceAdapterRegistry
from services.crm.deal_source_mutation import (
    CashSourceEdit,
    DealSourceMutationService,
    ExchangeSourceEdit,
)
from services.crm.deal_status_policy import DealStatusPolicy
from services.crm.exchange_deal_source_adapter import ExchangeDealSourceAdapter
from services.exchange.balance_service import ExchangeBalanceService
from tests.db.conftest import balance_of

CLIENT_CHAT = -100500
REQUEST_CHAT = -777201


def _source_mutation_service(pool, event_bus: DealEventBus):
    unit_of_work_factory = partial(AsyncpgUnitOfWork, pool)
    balance_service = ExchangeBalanceService(unit_of_work_factory)
    router = RequestRouterService(
        request_chat_id=REQUEST_CHAT,
        city_cash_chats={"екб": REQUEST_CHAT},
        city_schedule_chats={},
        default_city="екб",
    )
    schedule_repo = RequestScheduleRepo(pool)
    schedule = RequestScheduleService(repo=schedule_repo, router_service=router)
    cash_context = ClientWalletScheduleContextAdapter(ClientsRepo(pool), schedule_repo)
    deals = DealService(
        DealRepository(pool),
        event_bus,
        DealStatusPolicy(DealWorkflowRepository(pool)),
    )
    repository = DealSourceRepositoryAdapter(
        DealSourceRecordReaderAdapter(ExchangeRequestsRepo(pool), schedule_repo)
    )
    cash_edit = EditCashRequest(
        repo=cash_context,
        router_service=router,
        schedule_service=schedule,
    )
    return DealSourceMutationService(
        deal_service=deals,
        unit_of_work_factory=unit_of_work_factory,
        adapters=DealSourceAdapterRegistry(
            (
                ExchangeDealSourceAdapter(
                    repository=repository,
                    balance_service=balance_service,
                ),
                CashDealSourceAdapter(
                    repository=repository,
                    cash_edit=cash_edit,
                ),
            )
        ),
    )


async def _seed_exchange(pool, repo, client_id: int, *, suffix: str):
    req_id = f"CRM-{suffix}"
    await repo.deposit(
        client_id=client_id,
        currency_code="USDT",
        amount=Decimal("100"),
        source="test",
        idempotency_key=f"{suffix}:recv",
    )
    await repo.withdraw(
        client_id=client_id,
        currency_code="RUB",
        amount=Decimal("9000"),
        source="test",
        idempotency_key=f"{suffix}:pay",
    )
    await ExchangeRequestsRepo(pool).upsert_exchange_request_link(
        client_req_id=req_id,
        table_req_id=f"70{suffix[-1]}",
        client_chat_id=CLIENT_CHAT,
        client_message_id=101,
        request_chat_id=REQUEST_CHAT,
        request_message_id=202,
        request_text=(
            f"<b>Заявка</b>: <code>{req_id}</code>\n"
            "<b>Получаем</b>: <code>100 usdt</code>\n"
            "<b>Курс</b>: <code>90</code>\n"
            "<b>Отдаём</b>: <code>9’000 rub</code>"
        ),
        table_in_cur="USDT",
        table_in_amount=Decimal("100"),
        table_out_cur="RUB",
        table_out_amount=Decimal("9000"),
        table_rate=Decimal("90"),
        status="active",
    )
    deal, _ = await DealRepository(pool).create_deal_idempotent(
        DealCreateCommand(
            deal_type="sale",
            city="екб",
            actor_user_id=None,
            client_id=client_id,
            source="tg_bot",
            source_kind="exchange",
            source_ref=f"test:{suffix}",
            exchange_client_req_id=req_id,
            body={
                "client_name": "Тестовый чат",
                "client_req_id": req_id,
                "recv_code": "USDT",
                "recv_amount": "100",
                "pay_code": "RUB",
                "pay_amount": "9000",
                "rate": "90",
            },
        )
    )
    return req_id, deal


async def test_exchange_source_edit_commits_ledger_link_deal_outbox_and_event(
    pool, repo, client_id
) -> None:
    req_id, deal = await _seed_exchange(pool, repo, client_id, suffix="EDIT1")
    event_bus = DealEventBus()
    service = _source_mutation_service(pool, event_bus)

    async with event_bus.subscribe() as events:
        updated = await service.edit_exchange(
            deal.id,
            ExchangeSourceEdit(
                operation_id=501,
                recv_code="USDT",
                recv_amount=Decimal("150"),
                pay_code="RUB",
                pay_amount=Decimal("13500"),
                rate=Decimal("90"),
                note="CRM edit",
            ),
            actor_name="Manager",
        )
        event = events.get_nowait()

    link = await ExchangeRequestsRepo(pool).get_exchange_request_link(client_req_id=req_id)
    async with pool.acquire() as connection:
        outbox = await connection.fetch("SELECT kind, status FROM tg_outbox ORDER BY id")

    assert await balance_of(repo, client_id, "USDT") == Decimal("150.00")
    assert await balance_of(repo, client_id, "RUB") == Decimal("-13500.00")
    assert link is not None and link["table_in_amount"] == Decimal("150.00000000")
    assert "13500" in str(link["request_text"]).replace("’", "")
    assert updated.body.get("recv_amount") == "150"
    assert event.type == "deal.updated"
    assert [(row["kind"], row["status"]) for row in outbox] == [("deal_source_updated", "pending")]


async def test_exchange_source_cancel_commits_reversal_status_event_and_outbox(
    pool, repo, client_id
) -> None:
    req_id, deal = await _seed_exchange(pool, repo, client_id, suffix="CANCEL2")
    event_bus = DealEventBus()
    service = _source_mutation_service(pool, event_bus)
    async with pool.acquire() as connection:
        actor_user_id = await connection.fetchval(
            "INSERT INTO users (tg_user_id, display_name, role) "
            "VALUES (7002, 'CRM manager', 'manager') RETURNING id"
        )

    canceled = await service.cancel(
        deal.id,
        actor_user_id=actor_user_id,
        comment="CRM cancel",
    )

    link = await ExchangeRequestsRepo(pool).get_exchange_request_link(client_req_id=req_id)
    async with pool.acquire() as connection:
        events = await connection.fetch(
            "SELECT old_status, new_status FROM deal_status_events WHERE deal_id = $1 ORDER BY id",
            deal.id,
        )
        outbox = await connection.fetch("SELECT kind, status FROM tg_outbox ORDER BY id")

    assert await balance_of(repo, client_id, "USDT") == Decimal("0.00")
    assert await balance_of(repo, client_id, "RUB") == Decimal("0.00")
    assert link is not None and link["status"] == "cancelled"
    assert canceled.status == "canceled"
    assert [(row["old_status"], row["new_status"]) for row in events] == [
        (None, "new"),
        ("new", "canceled"),
    ]
    assert [(row["kind"], row["status"]) for row in outbox] == [("deal_status_changed", "pending")]


async def test_deal_write_failure_rolls_back_exchange_source_and_emits_no_event(
    pool, repo, client_id, monkeypatch
) -> None:
    req_id, deal = await _seed_exchange(pool, repo, client_id, suffix="FAIL3")
    event_bus = DealEventBus()
    service = _source_mutation_service(pool, event_bus)

    async def fail_update(*args, **kwargs):
        raise RuntimeError("deal write failed")

    monkeypatch.setattr(DealRepository, "update_deal", fail_update)
    async with event_bus.subscribe() as events:
        with pytest.raises(RuntimeError, match="deal write failed"):
            await service.edit_exchange(
                deal.id,
                ExchangeSourceEdit(
                    operation_id=503,
                    recv_code="USDT",
                    recv_amount=Decimal("150"),
                    pay_code="RUB",
                    pay_amount=Decimal("13500"),
                    rate=Decimal("90"),
                ),
                actor_name="Manager",
            )
        assert events.empty()

    link = await ExchangeRequestsRepo(pool).get_exchange_request_link(client_req_id=req_id)
    async with pool.acquire() as connection:
        outbox_count = await connection.fetchval("SELECT count(*) FROM tg_outbox")

    assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
    assert await balance_of(repo, client_id, "RUB") == Decimal("-9000.00")
    assert link is not None and link["table_in_amount"] == Decimal("100.00000000")
    assert outbox_count == 0


async def test_cash_source_edit_commits_schedule_deal_and_outbox_together(
    pool, repo, client_id
) -> None:
    req_id = "Б-654321"
    old_client_text = (
        f"Заявка на внесение: {req_id}\nГород: екб\nСумма: 1000 RUB\nКод: 111-222\nВремя: 10:00"
    )
    await RequestScheduleRepo(pool).upsert_request_schedule_entry(
        req_id=req_id,
        city="екб",
        hhmm="10:00",
        request_kind="dep",
        line_text="+1000 RUB — Тестовый чат",
        client_name="Тестовый чат",
        request_chat_id=REQUEST_CHAT,
        request_message_id=303,
    )
    deal, _ = await DealRepository(pool).create_deal_idempotent(
        DealCreateCommand(
            deal_type="deposit",
            city="екб",
            actor_user_id=None,
            client_id=client_id,
            source="tg_bot",
            source_kind="cash",
            source_ref="test:cash-edit",
            body={
                "client_name": "Тестовый чат",
                "req_id": req_id,
                "request_kind": "dep",
                "currency": "RUB",
                "amount": "1000",
                "telegram_client_chat_id": CLIENT_CHAT,
                "telegram_client_message_id": 404,
                "telegram_client_text": old_client_text,
                "telegram_request_text": "request card",
            },
        )
    )
    event_bus = DealEventBus()
    service = _source_mutation_service(pool, event_bus)

    updated = await service.edit_cash(
        deal.id,
        CashSourceEdit(
            city="екб",
            amount=Decimal("1500"),
            comment="CRM cash edit",
            contact1="@cashier",
            contact2="@client",
        ),
        actor_name="Manager",
    )

    schedule = await RequestScheduleRepo(pool).get_request_schedule_entry_by_req_id(req_id=req_id)
    async with pool.acquire() as connection:
        outbox = await connection.fetch("SELECT kind, status FROM tg_outbox ORDER BY id")

    assert schedule is not None
    assert schedule["hhmm"] == "10:00"
    assert "1500" in str(schedule["line_text"]).replace("’", "").replace(".00", "")
    assert updated.body.get("amount") == "1500"
    assert "1500" in str(updated.body.get("telegram_client_text")).replace("’", "")
    assert [(row["kind"], row["status"]) for row in outbox] == [("deal_source_updated", "pending")]
