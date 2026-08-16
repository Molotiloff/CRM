from __future__ import annotations

from decimal import Decimal

from db_asyncpg.repositories.clients import ClientsRepo
from db_asyncpg.repositories.exchange_requests import ExchangeRequestsRepo
from db_asyncpg.repositories.request_schedule import RequestScheduleRepo
from db_asyncpg.repositories.transactions import TransactionsRepo


async def test_narrow_client_and_transaction_repositories_share_ledger_contract(
    pool,
) -> None:
    clients = ClientsRepo(pool)
    transactions = TransactionsRepo(pool)
    client_id = await clients.ensure_client(-100901, "Contract client")
    await clients.add_currency(client_id, "rub", 2)

    deposit_id = await transactions.deposit(
        client_id=client_id,
        currency_code="rub",
        amount=Decimal("100.005"),
        source="contract",
        idempotency_key="contract:deposit",
    )
    repeated_id = await transactions.deposit(
        client_id=client_id,
        currency_code="RUB",
        amount=Decimal("100.005"),
        source="contract",
        idempotency_key="contract:deposit",
    )
    await transactions.withdraw(
        client_id=client_id,
        currency_code="RUB",
        amount=Decimal("40"),
        source="contract",
        idempotency_key="contract:withdraw",
    )

    wallet = await clients.snapshot_wallet(client_id)
    rub = next(row for row in wallet if row["currency_code"] == "RUB")
    async with pool.acquire() as connection:
        transaction_count = await connection.fetchval(
            "SELECT count(*) FROM transactions WHERE client_id = $1",
            client_id,
        )

    assert repeated_id == deposit_id
    assert rub["balance"] == Decimal("60.01")
    assert transaction_count == 2


async def test_exchange_request_repository_preserves_partial_upsert_contract(pool) -> None:
    repository = ExchangeRequestsRepo(pool)
    await repository.upsert_exchange_request_link(
        client_req_id="R0-EXCHANGE",
        table_req_id="501",
        client_chat_id=-100902,
        client_message_id=10,
        table_in_cur="USDT",
        table_in_amount=Decimal("100"),
        status="active",
    )
    await repository.upsert_exchange_request_link(
        client_req_id="R0-EXCHANGE",
        table_req_id="501",
        request_chat_id=-100903,
        request_message_id=20,
        status="cancelled",
    )

    by_client_id = await repository.get_exchange_request_link(client_req_id="R0-EXCHANGE")
    by_table_id = await repository.get_exchange_request_link_by_table_req_id(table_req_id="501")

    assert by_client_id == by_table_id
    assert by_client_id is not None
    assert by_client_id["client_chat_id"] == -100902
    assert by_client_id["client_message_id"] == 10
    assert by_client_id["request_chat_id"] == -100903
    assert by_client_id["request_message_id"] == 20
    assert by_client_id["status"] == "cancelled"


async def test_request_schedule_repository_normalizes_and_deactivates_contract(pool) -> None:
    repository = RequestScheduleRepo(pool)
    await repository.upsert_request_schedule_entry(
        req_id="R0-CASH",
        city=" ЕКБ ",
        hhmm="12:30",
        request_kind="dep",
        line_text="+1000 RUB — Contract client",
        client_name="Contract client",
        request_chat_id=-100904,
        request_message_id=30,
    )

    entry = await repository.get_request_schedule_entry_by_req_id(req_id="R0-CASH")
    active = await repository.list_request_schedule_entries(city="ЕКБ")
    deactivated = await repository.deactivate_request_schedule_entry("R0-CASH")
    active_after = await repository.list_request_schedule_entries(city="екб")

    assert entry is not None and entry["city"] == "екб"
    assert [row["req_id"] for row in active] == ["R0-CASH"]
    assert deactivated is True
    assert active_after == []


async def test_repository_bound_to_connection_joins_outer_transaction(pool) -> None:
    chat_id = -100905
    async with pool.acquire() as connection:
        transaction = connection.transaction()
        await transaction.start()
        repository = ClientsRepo(pool, connection=connection)

        client_id = await repository.ensure_client(chat_id, "Bound client")
        inside_transaction = await repository.find_client_by_name_exact("Bound client")

        assert inside_transaction is not None
        assert inside_transaction["id"] == client_id
        await transaction.rollback()

    persisted = await ClientsRepo(pool).find_client_by_name_exact("Bound client")
    assert persisted is None
