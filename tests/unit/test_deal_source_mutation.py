from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from domain import (
    CashRequestKind,
    CityCode,
    Deal,
    ExchangeRequestSource,
    ExchangeRequestStatus,
    Money,
    ScheduleEntry,
    TelegramMessageRef,
)
from services.cash_requests import EditCashRequestResult
from services.crm.cash_deal_source_adapter import CashDealSourceAdapter
from services.crm.deal_service import DealValidationError
from services.crm.deal_source_adapter import DealSourceAdapterRegistry
from services.crm.deal_source_mutation import (
    CashSourceEdit,
    DealSourceMutationService,
    ExchangeSourceEdit,
)
from services.crm.exchange_deal_source_adapter import ExchangeDealSourceAdapter


class FakeDeals:
    def __init__(
        self,
        *,
        status: str = "new",
        source_kind: str = "exchange",
        cash_kind: CashRequestKind = CashRequestKind.DEPOSIT,
    ) -> None:
        now = datetime.now(UTC)
        body = {
            "client_req_id": "51374723",
            "recv_code": "RUB",
            "recv_amount": "9000",
            "pay_code": "USDT",
            "pay_amount": "100",
            "rate": "90",
        }
        if source_kind == "cash":
            body: dict = {
                "req_id": "Б-123456",
                "request_kind": str(cash_kind),
                "telegram_client_chat_id": -100,
                "telegram_client_message_id": 44,
                "telegram_client_text": "client card",
                "telegram_request_text": "request card",
            }
            if cash_kind is CashRequestKind.EXCHANGE:
                body.update(
                    {
                        "in_code": "RUB",
                        "in_amount": "9000",
                        "out_code": "USDT",
                        "out_amount": "100",
                    }
                )
            else:
                body.update({"currency": "RUB", "amount": "1000"})
        self.row = {
            "id": 7,
            "deal_no": 100007,
            "deal_type": "sale",
            "city": "екб",
            "client_id": 9,
            "client_name": "Client",
            "status": status,
            "source": "tg_bot",
            "source_kind": source_kind,
            "exchange_client_req_id": ("51374723" if source_kind == "exchange" else None),
            "body": body,
            "created_at": now,
            "updated_at": now,
        }
        self.published: list[tuple[str, dict]] = []

    async def get_deal(self, deal_id: int):
        return Deal.from_record(self.row)

    async def publish_committed_update(self, deal):
        self.published.append(("updated", deepcopy(deal)))

    async def publish_committed_status_change(self, deal, **kwargs):
        self.published.append(("status", {"deal": deepcopy(deal), **kwargs}))


class FakeTransactionalDeals:
    def __init__(self, owner: FakeDeals) -> None:
        self._owner = owner
        self.update_call = None
        self.status_call = None

    async def update_deal(self, deal_id: int, command):
        changes = command.to_changes()
        self.update_call = deepcopy(changes)
        self._owner.row.update(deepcopy(changes))
        return Deal.from_record(self._owner.row)

    async def change_status(self, deal_id: int, command):
        self.status_call = command
        self._owner.row["status"] = command.status
        return Deal.from_record(self._owner.row), True


class FakeExchangeRequests:
    def __init__(self) -> None:
        self.upsert_call = None
        self.status_call = None

    async def upsert_exchange_request_link(self, **kwargs):
        self.upsert_call = deepcopy(kwargs)

    async def set_exchange_request_status(self, **kwargs):
        self.status_call = deepcopy(kwargs)
        return True


class FakeRequestSchedule:
    def __init__(self) -> None:
        self.upsert_call = None
        self.deactivated = None

    async def upsert_request_schedule_entry(self, **kwargs):
        self.upsert_call = deepcopy(kwargs)

    async def deactivate_request_schedule_entry(self, req_id: str):
        self.deactivated = req_id
        return True


class FakeUnitOfWork:
    def __init__(self, deals: FakeDeals) -> None:
        self.deals = FakeTransactionalDeals(deals)
        self.exchange_requests = FakeExchangeRequests()
        self.request_schedule = FakeRequestSchedule()
        self.transactions = object()
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def commit(self):
        self.committed = True


class FakeUnitOfWorkFactory:
    def __init__(self, deals: FakeDeals) -> None:
        self._deals = deals
        self.created: list[FakeUnitOfWork] = []

    def __call__(self):
        unit_of_work = FakeUnitOfWork(self._deals)
        self.created.append(unit_of_work)
        return unit_of_work


class FakeRepository:
    def __init__(self, cash_kind: CashRequestKind = CashRequestKind.DEPOSIT) -> None:
        self._cash_kind = cash_kind

    async def get_exchange_source(self, *, request_id: str):
        return ExchangeRequestSource(
            request_id=request_id,
            table_request_id="101",
            receive=Money.from_raw("9000", "RUB"),
            pay=Money.from_raw("100", "USDT"),
            rate=Decimal("90"),
            status=ExchangeRequestStatus.ACTIVE,
            client_message=TelegramMessageRef(-100, 44),
            request_message=TelegramMessageRef(-777, 55),
        )

    async def get_schedule_entry(self, *, request_id: str):
        return ScheduleEntry(
            request_id=request_id,
            city=CityCode("екб"),
            hhmm="10:00",
            kind=self._cash_kind,
            line_text="+1000 RUB — Client",
            client_name="Client",
            request_message=TelegramMessageRef(-777, 55),
        )


class FakeBalanceService:
    def __init__(self) -> None:
        self.edit_call = None
        self.cancel_call = None

    async def apply_edit_delta(self, **kwargs):
        self.edit_call = dict(kwargs)
        return []

    async def apply_cancel(self, **kwargs):
        self.cancel_call = dict(kwargs)
        return "-", "+"


class FakeCashEdit:
    async def prepare_core(self, params, **kwargs):
        return EditCashRequestResult(
            ok=True,
            req_id="Б-123456",
            client_text="updated client card",
            request_text="updated request card",
            schedule_line="+1500 RUB — Client",
        )


def _service(
    deals: FakeDeals,
    *,
    cash_kind: CashRequestKind = CashRequestKind.DEPOSIT,
):
    factory = FakeUnitOfWorkFactory(deals)
    balance = FakeBalanceService()
    service = DealSourceMutationService(
        deal_service=deals,
        unit_of_work_factory=factory,
        adapters=DealSourceAdapterRegistry(
            (
                ExchangeDealSourceAdapter(
                    repository=FakeRepository(cash_kind),
                    balance_service=balance,
                ),
                CashDealSourceAdapter(
                    repository=FakeRepository(cash_kind),
                    cash_edit=FakeCashEdit(),
                ),
            )
        ),
    )
    return service, factory, balance


async def test_exchange_cancel_commits_source_and_status_before_event() -> None:
    deals = FakeDeals()
    service, factory, balance = _service(deals)

    result = await service.cancel(7, actor_user_id=3, comment="duplicate")

    unit_of_work = factory.created[0]
    assert unit_of_work.committed is True
    assert balance.cancel_call["unit_of_work"] is unit_of_work
    assert unit_of_work.exchange_requests.status_call == {
        "client_req_id": "51374723",
        "status": "cancelled",
    }
    assert unit_of_work.deals.status_call.expected_old_status == "new"
    assert result.status == "canceled"
    assert deals.published[0][0] == "status"


async def test_exchange_edit_commits_balances_link_deal_and_outbox_trigger() -> None:
    deals = FakeDeals()
    service, factory, balance = _service(deals)

    updated = await service.edit_exchange(
        7,
        ExchangeSourceEdit(
            operation_id=501,
            recv_code="rub",
            recv_amount=Decimal("13500"),
            pay_code="usdt",
            pay_amount=Decimal("150"),
            rate=Decimal("90"),
            note="changed",
        ),
        actor_name="Manager",
    )

    unit_of_work = factory.created[0]
    assert unit_of_work.committed is True
    assert balance.edit_call["unit_of_work"] is unit_of_work
    assert unit_of_work.exchange_requests.upsert_call["table_in_amount"] == Decimal("13500")
    assert "Получаем" in unit_of_work.exchange_requests.upsert_call["request_text"]
    assert unit_of_work.deals.update_call["body"]["pay_amount"] == "150"
    assert updated.comment == "changed"
    assert deals.published[0][0] == "updated"


@pytest.mark.parametrize("status", ["done", "canceled"])
async def test_terminal_exchange_cannot_be_edited(status: str) -> None:
    service, factory, _ = _service(FakeDeals(status=status))

    with pytest.raises(DealValidationError, match="cannot be edited"):
        await service.edit_exchange(
            7,
            ExchangeSourceEdit(
                operation_id=1,
                recv_code="RUB",
                recv_amount=Decimal("1"),
                pay_code="USDT",
                pay_amount=Decimal("1"),
                rate=Decimal("1"),
            ),
            actor_name="Manager",
        )

    assert factory.created == []


async def test_cash_edit_and_cancel_use_schedule_inside_unit_of_work() -> None:
    deals = FakeDeals(source_kind="cash")
    service, factory, _ = _service(deals)

    updated = await service.edit_cash(
        7,
        CashSourceEdit(city="екб", amount=Decimal("1500"), comment="changed"),
        actor_name="Manager",
    )
    edit_uow = factory.created[0]

    assert edit_uow.committed is True
    assert edit_uow.request_schedule.upsert_call["line_text"] == "+1500 RUB — Client"
    assert edit_uow.deals.update_call["body"]["amount"] == "1500"
    assert updated.body.get("telegram_client_text") == "updated client card"

    canceled = await service.cancel(7, actor_user_id=3)
    cancel_uow = factory.created[1]

    assert cancel_uow.committed is True
    assert cancel_uow.request_schedule.deactivated == "Б-123456"
    assert canceled.status == "canceled"


async def test_cash_exchange_edit_uses_typed_currencies_and_amounts() -> None:
    deals = FakeDeals(source_kind="cash", cash_kind=CashRequestKind.EXCHANGE)
    service, factory, _ = _service(deals, cash_kind=CashRequestKind.EXCHANGE)

    updated = await service.edit_cash(
        7,
        CashSourceEdit(
            city="екб",
            in_amount=Decimal("13500"),
            out_amount=Decimal("150"),
        ),
        actor_name="Manager",
    )

    update = factory.created[0].deals.update_call["body"]
    assert update["in_amount"] == "13500"
    assert update["out_amount"] == "150"
    assert updated.body.get("in_code") == "RUB"


async def test_cash_edit_rejects_body_and_schedule_kind_conflict() -> None:
    deals = FakeDeals(source_kind="cash")
    service, factory, _ = _service(deals, cash_kind=CashRequestKind.EXCHANGE)

    with pytest.raises(DealValidationError, match="does not match"):
        await service.edit_cash(
            7,
            CashSourceEdit(city="екб", amount=Decimal("1500")),
            actor_name="Manager",
        )

    assert factory.created == []
