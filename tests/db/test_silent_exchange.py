import asyncio
from decimal import Decimal
from functools import partial
from unittest.mock import MagicMock

import pytest

from db_asyncpg.repositories.transactions import TransactionsRepo
from db_asyncpg.uow import AsyncpgUnitOfWork
from services.exchange.accept_short_service import AcceptShortCommand, AcceptShortService
from tests.db.conftest import balance_of, tx_rows


def service(pool, repo):
    return AcceptShortService(
        repo=repo, unit_of_work_factory=partial(AsyncpgUnitOfWork, pool),
        source_links=MagicMock(), keyboards=MagicMock(),
    )


def command(text="/пр 2000 от 2000/83"):
    return AcceptShortCommand(chat_id=-100500, chat_name="BS", message_id=101, text=text, actor_name="Partner")


async def test_silent_exchange_is_atomic_idempotent_and_does_not_create_requests(pool, repo, client_id):
    exchange = service(pool, repo)
    await asyncio.gather(*[exchange.execute_silent_balance(command()) for _ in range(4)])
    assert await balance_of(repo, client_id, "RUB") == Decimal("-2000")
    assert await balance_of(repo, client_id, "USDT") == Decimal("24.10")
    assert len(await tx_rows(pool, client_id)) == 2
    async with pool.acquire() as con:
        for table in ("deals", "exchange_request_links", "tg_outbox", "usdt_fulfillment_queue"):
            assert await con.fetchval(f"SELECT count(*) FROM {table}") == 0


async def test_silent_exchange_rolls_back_first_leg_if_second_fails(pool, repo, client_id, monkeypatch):
    original = TransactionsRepo.deposit

    async def failed(self, **kwargs):
        await original(self, **kwargs)
        raise RuntimeError("after second leg")

    monkeypatch.setattr(TransactionsRepo, "deposit", failed)
    with pytest.raises(RuntimeError):
        await service(pool, repo).execute_silent_balance(command())
    assert await balance_of(repo, client_id, "RUB") == 0
    assert await balance_of(repo, client_id, "USDT") == 0
    assert not await tx_rows(pool, client_id)


@pytest.mark.parametrize("text", ["/пр 0 от 1", "/пр 2000 от broken", "/пр 2000 ое 20"])
async def test_invalid_or_missing_account_silent_exchange_writes_no_transactions(pool, repo, client_id, text):
    with pytest.raises(ValueError):
        await service(pool, repo).execute_silent_balance(command(text))
    assert not await tx_rows(pool, client_id)
