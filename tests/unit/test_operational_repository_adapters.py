from unittest.mock import AsyncMock, MagicMock

import pytest

from db_asyncpg.adapters import (
    ClientWalletScheduleContextAdapter,
    ClientWalletTransactionRepositoryAdapter,
    DealSourceRecordReaderAdapter,
)


@pytest.mark.asyncio
async def test_client_transaction_adapter_delegates_to_single_owners() -> None:
    clients = MagicMock()
    clients.ensure_client = AsyncMock(return_value=41)
    transactions = MagicMock()
    transactions.deposit = AsyncMock(return_value=73)
    adapter = ClientWalletTransactionRepositoryAdapter(clients, transactions)

    client_id = await adapter.ensure_client(-100, "Client")
    transaction_id = await adapter.deposit(client_id=client_id, amount=10)

    assert client_id == 41
    assert transaction_id == 73
    clients.ensure_client.assert_awaited_once_with(-100, "Client", None)
    transactions.deposit.assert_awaited_once_with(client_id=41, amount=10)


@pytest.mark.asyncio
async def test_schedule_context_adapter_exposes_only_command_context() -> None:
    clients = MagicMock()
    clients.snapshot_wallet = AsyncMock(return_value=[{"currency_code": "RUB"}])
    schedule = MagicMock()
    schedule.next_request_id = AsyncMock(return_value=17)
    adapter = ClientWalletScheduleContextAdapter(clients, schedule)

    assert await adapter.snapshot_wallet(5) == [{"currency_code": "RUB"}]
    assert await adapter.next_request_id() == 17
    assert not hasattr(adapter, "deposit")


@pytest.mark.asyncio
async def test_deal_source_reader_routes_each_source_to_its_repository() -> None:
    exchange = MagicMock()
    exchange.get_exchange_request_link = AsyncMock(return_value={"client_req_id": "E-1"})
    schedule = MagicMock()
    schedule.get_request_schedule_entry_by_req_id = AsyncMock(return_value={"req_id": "C-1"})
    reader = DealSourceRecordReaderAdapter(exchange, schedule)

    assert await reader.get_exchange_request_link(client_req_id="E-1") == {"client_req_id": "E-1"}
    assert await reader.get_request_schedule_entry_by_req_id(req_id="C-1") == {"req_id": "C-1"}
