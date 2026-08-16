from decimal import Decimal

import pytest

from db_asyncpg.adapters import DealSourceRepositoryAdapter
from domain import (
    CashRequestKind,
    CityCode,
    CurrencyCode,
    DomainStateError,
    ExchangeRequestStatus,
    TelegramMessageRef,
)


async def test_adapter_maps_exchange_and_schedule_records() -> None:
    adapter = DealSourceRepositoryAdapter(FakeRecordReader())

    exchange = await adapter.get_exchange_source(request_id="51374723")
    schedule = await adapter.get_schedule_entry(request_id="Б-123456")

    assert exchange is not None
    assert exchange.receive.currency == CurrencyCode("RUB")
    assert exchange.receive.amount == Decimal("9000.00")
    assert exchange.status is ExchangeRequestStatus.ACTIVE
    assert exchange.primary_message == TelegramMessageRef(-100, 44)
    assert schedule is not None
    assert schedule.city == CityCode("екб")
    assert schedule.kind is CashRequestKind.DEPOSIT


async def test_adapter_returns_none_for_missing_source() -> None:
    adapter = DealSourceRepositoryAdapter(FakeRecordReader(missing=True))

    assert await adapter.get_exchange_source(request_id="missing") is None
    assert await adapter.get_schedule_entry(request_id="missing") is None


async def test_adapter_reports_corrupt_persisted_source_as_domain_state_error() -> None:
    adapter = DealSourceRepositoryAdapter(FakeRecordReader(corrupt=True))

    with pytest.raises(DomainStateError, match="Invalid persisted exchange"):
        await adapter.get_exchange_source(request_id="51374723")


class FakeRecordReader:
    def __init__(self, *, missing: bool = False, corrupt: bool = False) -> None:
        self._missing = missing
        self._corrupt = corrupt

    async def get_exchange_request_link(self, *, client_req_id: str):
        if self._missing:
            return None
        return {
            "client_req_id": client_req_id,
            "table_req_id": "101",
            "table_in_cur": "RUB",
            "table_in_amount": "9000.00",
            "table_out_cur": "USDT",
            "table_out_amount": "100",
            "table_rate": "90",
            "status": "invalid" if self._corrupt else "active",
            "client_chat_id": -100,
            "client_message_id": 44,
        }

    async def get_request_schedule_entry_by_req_id(self, *, req_id: str):
        if self._missing:
            return None
        return {
            "req_id": req_id,
            "city": "екб",
            "hhmm": "10:00",
            "request_kind": "dep",
            "line_text": "+1000 RUB - Client",
            "client_name": "Client",
            "request_chat_id": -777,
            "request_message_id": 55,
        }
