"""Фикстуры для тестов против настоящего Postgres (tests/db)."""

from __future__ import annotations

import shutil
import subprocess
from decimal import Decimal

import asyncpg
import pytest
import pytest_asyncio

from db_asyncpg.adapters import (
    ClientWalletScheduleContextAdapter,
    ClientWalletTransactionRepositoryAdapter,
)
from db_asyncpg.migrator import AlembicMigrator
from db_asyncpg.pool import close_pool, create_pool
from db_asyncpg.repositories import (
    ActCounterRepo,
    ClientsRepo,
    ExchangeRequestsRepo,
    ManagersRepo,
    PaymentWatchRepo,
    RequestScheduleRepo,
    TransactionsRepo,
)
from tests.conftest import database_user, managed_test_database_url

# Корневые и независимые таблицы, которые меняют DB-тесты. CASCADE подчищает
# transactions, accounts, deals и events; schedule/source таблицы не имеют FK к
# clients, поэтому перечислены явно.
_TRUNCATE_SQL = """
TRUNCATE
    partner_purchase_allocations,
    partner_transfer_watches,
    profit_usdt_accruals,
    usdt_fulfillment_queue,
    deal_settlements,
    firm_wallet_fact_snapshots,
    firm_wallet_addresses,
    firm_wallet_facts,
    cash_chat_registry,
    cash_desks,
    internal_account_moves,
    internal_accounts,
    expenses,
    capital_moves,
    capital_owners,
    firm_position_moves,
    clients,
    tg_outbox,
    request_schedule_entries,
    request_schedule_boards,
    exchange_request_links
RESTART IDENTITY CASCADE
"""


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url, managed = managed_test_database_url()
    if not managed:
        return url

    if not (shutil.which("createdb") and shutil.which("dropdb")):
        pytest.skip("createdb/dropdb не найдены — задайте TEST_DATABASE_URL")

    owner = database_user(url)
    db_name = url.rsplit("/", 1)[1]
    subprocess.run(["dropdb", "--if-exists", db_name], check=True, capture_output=True)
    created = subprocess.run(
        ["createdb", "-O", owner, db_name], check=False, capture_output=True, text=True
    )
    if created.returncode != 0:
        pytest.skip(f"Не удалось создать тестовую БД {db_name}: {created.stderr.strip()}")

    yield url

    subprocess.run(["dropdb", "--if-exists", db_name], check=False, capture_output=True)


@pytest_asyncio.fixture(scope="session")
async def pool(test_database_url: str) -> asyncpg.Pool:
    await AlembicMigrator(test_database_url).upgrade_to_head()
    pool = await create_pool(test_database_url)
    yield pool
    await close_pool(pool)


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(pool: asyncpg.Pool):
    async with pool.acquire() as con:
        await con.execute(_TRUNCATE_SQL)
    yield


@pytest.fixture
def clients_repo(pool) -> ClientsRepo:
    return ClientsRepo(pool)


@pytest.fixture
def transactions_repo(pool) -> TransactionsRepo:
    return TransactionsRepo(pool)


@pytest.fixture
def managers_repo(pool) -> ManagersRepo:
    return ManagersRepo(pool)


@pytest.fixture
def repo(
    clients_repo: ClientsRepo,
    transactions_repo: TransactionsRepo,
) -> ClientWalletTransactionRepositoryAdapter:
    return ClientWalletTransactionRepositoryAdapter(clients_repo, transactions_repo)


@pytest.fixture
def request_schedule_repo(pool) -> RequestScheduleRepo:
    return RequestScheduleRepo(pool)


@pytest.fixture
def cash_request_repo(
    clients_repo: ClientsRepo,
    request_schedule_repo: RequestScheduleRepo,
) -> ClientWalletScheduleContextAdapter:
    return ClientWalletScheduleContextAdapter(clients_repo, request_schedule_repo)


@pytest.fixture
def exchange_requests_repo(pool) -> ExchangeRequestsRepo:
    return ExchangeRequestsRepo(pool)


@pytest.fixture
def act_counter_repo(pool) -> ActCounterRepo:
    return ActCounterRepo(pool)


@pytest.fixture
def payment_watch_repo(pool) -> PaymentWatchRepo:
    return PaymentWatchRepo(pool)


@pytest_asyncio.fixture
async def client_id(repo: ClientWalletTransactionRepositoryAdapter) -> int:
    """Клиент со счетами RUB/USDT (prec 2) и BTC (prec 8)."""
    cid = await repo.ensure_client(chat_id=-100500, name="Тестовый чат")
    for code, prec in (("RUB", 2), ("USDT", 2), ("BTC", 8)):
        await repo.add_currency(cid, code, prec)
    return cid


async def balance_of(
    repo: ClientWalletTransactionRepositoryAdapter,
    client_id: int,
    code: str,
) -> Decimal:
    rows = await repo.snapshot_wallet(client_id)
    acc = next(r for r in rows if str(r["currency_code"]).upper() == code.upper())
    return Decimal(str(acc["balance"]))


async def tx_rows(pool: asyncpg.Pool, client_id: int, code: str | None = None) -> list[dict]:
    async with pool.acquire() as con:
        rows = await con.fetch(
            """
            SELECT t.id, t.amount, t.balance_after, t.comment, t.source,
                   t.idempotency_key, a.currency_code
            FROM transactions t JOIN client_accounts a ON a.id = t.account_id
            WHERE t.client_id = $1 AND ($2::text IS NULL OR a.currency_code = $2)
            ORDER BY t.id
            """,
            client_id,
            code.upper() if code else None,
        )
    return [dict(r) for r in rows]
