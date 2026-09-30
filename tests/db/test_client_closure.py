from decimal import Decimal

import pytest

from db_asyncpg.repositories.client_closure import ClientClosureError, ClientClosureRepo
from db_asyncpg.repositories.clients import ClientsRepo


@pytest.mark.asyncio
async def test_zero_client_posts_corrections_and_closes_all_accounts(pool) -> None:
    clients = ClientsRepo(pool)
    client_id = await clients.ensure_client(-100701, "Старый чат")
    await clients.add_currency(client_id, "RUB", 2)
    await clients.add_currency(client_id, "USDT", 2)
    await clients.add_currency(client_id, "USD", 2)
    await clients.remove_currency(client_id, "USD")
    async with pool.acquire() as con:
        await con.execute(
            "UPDATE client_accounts SET balance = CASE currency_code "
            "WHEN 'RUB' THEN -9.8 WHEN 'USDT' THEN 35.63 ELSE 0 END "
            "WHERE client_id=$1", client_id,
        )
    closure = ClientClosureRepo(pool)
    preview = await closure.preview(-100701)
    assert len(preview.accounts) == 3
    await closure.close(
        chat_id=-100701, expected_fingerprint=preview.fingerprint,
        actor_tg_user_id=1234, confirmation_message_id=5678,
    )
    async with pool.acquire() as con:
        client = await con.fetchrow(
            "SELECT is_active, deactivated_at, closed_at FROM clients WHERE id=$1", client_id
        )
        accounts = await con.fetch(
            "SELECT currency_code, balance, is_active FROM client_accounts "
            "WHERE client_id=$1 ORDER BY currency_code", client_id
        )
        corrections = await con.fetch(
            "SELECT a.currency_code, t.amount, t.balance_after, t.source "
            "FROM transactions t JOIN client_accounts a ON a.id=t.account_id "
            "WHERE t.client_id=$1 ORDER BY a.currency_code", client_id
        )
    assert client["is_active"] is False
    assert client["deactivated_at"] is not None and client["closed_at"] is not None
    assert all(a["balance"] == 0 and not a["is_active"] for a in accounts)
    assert [(r["currency_code"], r["amount"], r["balance_after"], r["source"])
            for r in corrections] == [
        ("RUB", Decimal("9.80000000"), Decimal("0"), "client_zero"),
        ("USDT", Decimal("-35.63000000"), Decimal("0"), "client_zero"),
    ]
    with pytest.raises(ClientClosureError):
        await closure.close(
            chat_id=-100701, expected_fingerprint=preview.fingerprint,
            actor_tg_user_id=1234, confirmation_message_id=5678,
        )
    with pytest.raises(ValueError, match="обнулён"):
        await clients.ensure_client(-100701, "Старый чат")
    with pytest.raises(ValueError, match="обнулён"):
        await clients.add_currency(client_id, "THB", 2)


@pytest.mark.asyncio
async def test_zero_client_rejects_stale_confirmation_without_mutation(pool) -> None:
    clients = ClientsRepo(pool)
    client_id = await clients.ensure_client(-100702, "Другой чат")
    await clients.add_currency(client_id, "RUB", 2)
    closure = ClientClosureRepo(pool)
    preview = await closure.preview(-100702)
    async with pool.acquire() as con:
        await con.execute(
            "UPDATE client_accounts SET balance=100 WHERE client_id=$1", client_id
        )
    with pytest.raises(ClientClosureError, match="изменились"):
        await closure.close(
            chat_id=-100702, expected_fingerprint=preview.fingerprint,
            actor_tg_user_id=1234, confirmation_message_id=5678,
        )
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT is_active FROM clients WHERE id=$1", client_id)
        assert await con.fetchval(
            "SELECT balance FROM client_accounts WHERE client_id=$1", client_id
        ) == Decimal("100")
