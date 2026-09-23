from __future__ import annotations

from decimal import Decimal

import pytest

from api.read_repositories import BalanceReadRepository, ClientReadRepository


@pytest.mark.asyncio
async def test_crm_read_repository_returns_clients_stats_and_balances(pool, repo) -> None:
    client_id = await repo.ensure_client(chat_id=-1001, name="Read API Client", client_group="VIP")
    await repo.add_currency(client_id, "RUB", 2)
    await repo.add_currency(client_id, "USDT", 2)
    await repo.deposit(client_id=client_id, currency_code="RUB", amount=1000, source="test")
    await repo.withdraw(client_id=client_id, currency_code="RUB", amount=250, source="test")
    await repo.deposit(client_id=client_id, currency_code="USDT", amount=10, source="test")

    read_repo = ClientReadRepository(pool)

    clients = await read_repo.list_clients(search="Read API", limit=10)
    balances = await read_repo.balances_for_clients([client_id])
    stats = await read_repo.stats_for_clients([client_id])
    recent = await read_repo.recent_transactions_by_client([client_id])

    assert [row["id"] for row in clients] == [client_id]
    assert {row["currency_code"]: row["balance"] for row in balances} == {
        "RUB": Decimal("750.00000000"),
        "USDT": Decimal("10.00000000"),
    }
    assert stats[client_id]["deals_count"] == 3
    assert stats[client_id]["turnover_rub"] == Decimal("1250.00000000")
    assert recent[client_id][0]["currency_code"] == "USDT"


@pytest.mark.asyncio
async def test_crm_read_repository_uses_latest_rub_rate_for_nonzero_balances(
    pool, repo, exchange_requests_repo
) -> None:
    client_id = await repo.ensure_client(chat_id=-1002, name="Balance API Client")
    await repo.add_currency(client_id, "USDT", 2)
    await repo.deposit(client_id=client_id, currency_code="USDT", amount=10, source="test")
    await exchange_requests_repo.upsert_exchange_request_link(
        client_req_id="rate-1",
        table_req_id="rate-1",
        table_in_cur="USDT",
        table_out_cur="RUB",
        table_in_amount=10,
        table_out_amount=800,
        table_rate=80,
    )

    read_repo = BalanceReadRepository(pool)

    rows = await read_repo.nonzero_balances(currency="USDT")
    rates = await read_repo.latest_rub_rates()

    assert rows[0]["client_id"] == client_id
    assert rates["USDT"] == Decimal("80.00000000")


@pytest.mark.asyncio
async def test_balance_read_repository_excludes_internal_wallets(pool, repo) -> None:
    client_id = await repo.ensure_client(chat_id=-1003, name="Client balance")
    internal_id = await repo.ensure_client(
        chat_id=-1004, name="Operations wallet", client_group="internal_wallet"
    )
    for account_client_id in (client_id, internal_id):
        await repo.add_currency(account_client_id, "USDT", 2)
        await repo.deposit(
            client_id=account_client_id,
            currency_code="USDT",
            amount=10,
            source="test",
        )

    rows = await BalanceReadRepository(pool).nonzero_balances(currency="USDT")

    assert [row["client_id"] for row in rows] == [client_id]
