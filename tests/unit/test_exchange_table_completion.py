from contextlib import asynccontextmanager
from decimal import Decimal
from itertools import permutations

import pytest

from services.request_table.table_done_service import RequestTableDoneService


class RequestRepo:
    def __init__(self, recv: str, pay: str, recv_amount: str, pay_amount: str):
        self.row = {
            "table_req_id": "98765",
            "table_in_cur": recv,
            "table_out_cur": pay,
            "table_in_amount": recv_amount,
            "table_out_amount": pay_amount,
            "table_rate": "80",
            "is_table_done": False,
            "status": "active",
        }

    @asynccontextmanager
    async def table_write_lock(self, table_req_id: str):
        assert table_req_id == "98765"
        yield

    async def get_exchange_request_link_by_table_req_id(self, *, table_req_id: str):
        assert table_req_id == "98765"
        return dict(self.row)

    async def mark_exchange_request_table_done(self, *, table_req_id: str, is_table_done: bool):
        self.row["is_table_done"] = is_table_done
        return True


class Gateway:
    def __init__(self):
        self.buy: list[dict] = []
        self.sale: list[dict] = []

    async def read_main_rate(self, code, cell_map):
        assert code in cell_map
        return Decimal("100")

    async def append_buy_row(self, **kwargs):
        self.buy.append(kwargs)
        return 1

    async def append_sale_row(self, **kwargs):
        self.sale.append(kwargs)
        return 1, None


class DealService:
    def __init__(self):
        self.calls: list[str] = []

    async def complete_exchange_after_table(self, table_req_id: str, *, actor_user_id=None):
        self.calls.append(table_req_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recv", "pay", "recv_amount", "pay_amount", "buy_count", "sale_count"),
    [
        ("RUB", "USD", "1000", "12.5", 0, 1),
        ("USD", "RUB", "12.5", "1000", 1, 0),
        ("RUB", "EUR500", "1000", "10", 0, 1),
        ("EUR500", "RUB", "10", "1000", 1, 0),
        *[(recv, pay, "10", "20", 1, 1) for recv, pay in permutations(
            ("USD", "EUR", "EUR500", "USDW", "THB", "USDT"), 2
        )],
    ],
)
async def test_purchase_and_sale_written_only_once(
    recv, pay, recv_amount, pay_amount, buy_count, sale_count
) -> None:
    repo = RequestRepo(recv, pay, recv_amount, pay_amount)
    gateway = Gateway()
    deals = DealService()
    service = RequestTableDoneService(sheets_gateway=gateway)
    first = await service.write_exchange_request_once(
        table_req_id="98765", repo=repo, deal_service=deals, message_dt=None
    )
    repeated = await service.write_exchange_request_once(
        table_req_id="98765", repo=repo, deal_service=deals, message_dt=None
    )
    assert first is not None
    assert repeated is None
    assert len(gateway.buy) == buy_count
    assert len(gateway.sale) == sale_count
    assert deals.calls == ["98765", "98765"]
    if gateway.buy:
        assert gateway.buy[0]["amount"] == Decimal(recv_amount)
    if gateway.sale:
        assert gateway.sale[0]["in_amount"] == Decimal(recv_amount)
        assert gateway.sale[0]["out_amount"] == Decimal(pay_amount)
    if recv == "EUR500" and gateway.buy:
        assert gateway.buy[0]["currency"] == "EUR"
    if pay == "EUR500" and gateway.sale:
        assert gateway.sale[0]["out_currency"] == "EUR"
