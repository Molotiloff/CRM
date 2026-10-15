from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from db_asyncpg.repositories.aml_billing import AMLBillingRepository
from tests.db.conftest import balance_of, tx_rows


async def test_aml_charges_usdt_once_even_below_zero(pool, repo, client_id):
    await repo.add_currency(client_id, "USDT", 2)
    async with pool.acquire() as con:
        chat_id = await con.fetchval("SELECT chat_id FROM clients WHERE id=$1", client_id)
    billing = AMLBillingRepository(pool)
    assert await billing.wallet_client(chat_id) == client_id
    await asyncio.gather(*[
        billing.charge(client_id=client_id, chat_id=chat_id, idempotency_key="aml:test:1")
        for _ in range(5)
    ])
    assert await balance_of(repo, client_id, "USDT") == Decimal("-1")
    assert len(await tx_rows(pool, client_id, "USDT")) == 1
    await billing.charge(client_id=client_id, chat_id=chat_id, idempotency_key="aml:test:2")
    assert await balance_of(repo, client_id, "USDT") == Decimal("-2")


async def test_aml_cannot_charge_different_or_inactive_chat(pool, repo, client_id):
    await repo.add_currency(client_id, "USDT", 2)
    billing = AMLBillingRepository(pool)
    with pytest.raises(ValueError):
        await billing.charge(client_id=client_id, chat_id=123456, idempotency_key="aml:bad")
    async with pool.acquire() as con:
        chat_id = await con.fetchval("SELECT chat_id FROM clients WHERE id=$1", client_id)
        await con.execute("UPDATE clients SET is_active=FALSE WHERE id=$1", client_id)
    assert await billing.wallet_client(chat_id) is None
    with pytest.raises(ValueError):
        await billing.charge(client_id=client_id, chat_id=chat_id, idempotency_key="aml:closed")
    assert not await tx_rows(pool, client_id, "USDT")


async def test_aml_requires_active_usdt_account(pool, repo):
    client_id = await repo.ensure_client(chat_id=-100501, name="Клиент без счетов")
    billing = AMLBillingRepository(pool)
    async with pool.acquire() as con:
        chat_id = await con.fetchval("SELECT chat_id FROM clients WHERE id=$1", client_id)
    assert await billing.wallet_client(chat_id) is None
    await repo.add_currency(client_id, "USDT", 2)
    async with pool.acquire() as con:
        await con.execute("UPDATE client_accounts SET is_active=FALSE WHERE client_id=$1", client_id)
    assert await billing.wallet_client(chat_id) is None
    with pytest.raises(KeyError, match="account not found"):
        await billing.charge(client_id=client_id, chat_id=chat_id, idempotency_key="aml:no-account")
    assert not await tx_rows(pool, client_id, "USDT")
