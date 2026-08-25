from decimal import Decimal

import pytest

from domain import CashRequestKind
from services.cash_requests import (
    CashCardParser,
    CashDealStatusCardPresenter,
    CashRequestCalculationError,
    CashRequestCalculator,
    CashScheduleCoordinator,
    ScheduleEntry,
)
from services.cash_requests.deal_status_workflow import (
    CANCEL_POLICY,
    DONE_POLICY,
    READY_POLICY,
    CashDealStatusWorkflow,
)
from services.cash_requests.parsing import ParsedRequest
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.workflow_models import CashRequestStatusCommand
from services.messaging import CollectingReplier
from tests.fakes import FakeMessenger


class FakeScheduleService:
    def __init__(self) -> None:
        self.entries: list[ScheduleEntry] = []
        self.removed: list[str] = []

    async def upsert_entry(self, entry: ScheduleEntry) -> None:
        self.entries.append(entry)

    async def remove_entry(self, *, req_id: str) -> bool:
        self.removed.append(req_id)
        return True


class FakeSettlementService:
    def __init__(self) -> None:
        self.ready_calls: list[tuple[str, int | None]] = []
        self.settled = False

    async def mark_ready(self, *, request_id: str, actor_tg_user_id: int | None):
        self.ready_calls.append((request_id, actor_tg_user_id))
        return 1

    async def is_settled(self, *, request_id: str) -> bool:
        return self.settled


class FakeCashKeyboards:
    def deal_actions(self, *, request_id: str):
        return ("ready", request_id)

    def completion_action(self, *, request_id: str):
        return ("done", request_id)


def _accounts() -> list[dict]:
    return [
        {"currency_code": "RUB", "precision": 2},
        {"currency_code": "USDT", "precision": 4},
    ]


def test_cash_calculator_builds_typed_single_currency_details() -> None:
    details = CashRequestCalculator().calculate(
        ParsedRequest(
            cmd="депр",
            kind="dep",
            city="екб",
            amount_expr="700 + 300",
            code="rub",
        ),
        _accounts(),
    )

    assert details.kind is CashRequestKind.DEPOSIT
    assert details.money is not None
    assert details.money.amount == Decimal("1000.00")
    assert str(details.money.currency) == "RUB"


def test_cash_calculator_rejects_currency_change_on_edit() -> None:
    with pytest.raises(CashRequestCalculationError, match="Нельзя менять валюты"):
        CashRequestCalculator().calculate(
            ParsedRequest(
                cmd="депр",
                kind="dep",
                city="екб",
                amount_expr="1000",
                code="RUB",
            ),
            _accounts(),
            expected_codes=("USDT",),
        )


def test_cash_card_parser_extracts_edit_snapshot_and_issue_data() -> None:
    parser = CashCardParser()
    card = "Заявка на внесение: Б-123456\nГород: екб\nСумма: 1000 RUB\nКод: 111-222\nВремя: 10:30"
    source = parser.edit_source(card)

    assert source is not None
    snapshot = parser.edit_snapshot(card, source=source, city="екб")
    assert snapshot is not None
    assert snapshot.expected_codes == ("RUB",)
    assert snapshot.hhmm == "10:30"
    assert parser.amount_and_code(card, kind="dep") == (Decimal("1000"), "RUB")
    assert parser.amount_and_code("Депозит: <code>1 250,50 usdt</code>", kind="dep") == (
        Decimal("1250.50"),
        "USDT",
    )
    assert parser.amount_and_code("Выдача: 100 RUB", kind="dep") is None


def test_status_presenter_replaces_previous_status_instead_of_duplicating_it() -> None:
    presenter = CashDealStatusCardPresenter()
    done = presenter.apply("Заявка\n----\nСоздал", DONE_POLICY)
    cancelled = presenter.apply(done, CANCEL_POLICY)

    assert "Сделка проведена" not in cancelled
    assert cancelled.count("Сделка отменена") == 1
    assert cancelled.index("Сделка отменена") < cancelled.index("Создал")


async def test_schedule_coordinator_owns_upsert_remove_and_board_sync() -> None:
    router = RequestRouterService(city_schedule_chats={"екб": -200})
    schedule = FakeScheduleService()
    coordinator = CashScheduleCoordinator(
        router_service=router,
        schedule_service=schedule,  # type: ignore[arg-type]
    )
    synced: list[str] = []

    async def sync(city: str) -> None:
        synced.append(city)

    entry = ScheduleEntry(
        req_id="Б-1",
        city="екб",
        hhmm=None,
        request_kind="dep",
        line_text="+100 RUB — Client",
        client_name="Client",
        request_chat_id=-100,
        request_message_id=1,
    )
    await coordinator.upsert(entry, sync_board=sync)
    removed = await coordinator.remove(req_id="Б-1", city="екб", sync_board=sync)

    assert schedule.entries == [entry]
    assert schedule.removed == ["Б-1"]
    assert removed is True
    assert synced == ["екб", "екб"]


async def test_cash_card_requires_ready_then_linked_settlement_before_done() -> None:
    router = RequestRouterService(
        request_chat_id=-100,
        city_cash_chats={"екб": -200},
        default_city="екб",
    )
    schedule = FakeScheduleService()
    settlements = FakeSettlementService()
    workflow = CashDealStatusWorkflow(
        router_service=router,
        schedule_coordinator=CashScheduleCoordinator(
            router_service=router,
            schedule_service=schedule,  # type: ignore[arg-type]
        ),
        keyboards=FakeCashKeyboards(),  # type: ignore[arg-type]
        settlement_service=settlements,  # type: ignore[arg-type]
    )
    messenger = FakeMessenger()
    replier = CollectingReplier()
    command = CashRequestStatusCommand(
        chat_id=-100,
        message_id=10,
        card_text="Заявка\n----\nСоздал",
        is_caption=False,
        callback_data="cash:deal_ready:req:Б-123456",
        actor_tg_user_id=77,
    )

    ready = await workflow.execute(
        command,
        req_id="Б-123456",
        policy=READY_POLICY,
        messenger=messenger,
        replier=replier,
    )
    blocked = await workflow.execute(
        command,
        req_id="Б-123456",
        policy=DONE_POLICY,
        messenger=messenger,
        replier=replier,
    )
    settlements.settled = True
    done = await workflow.execute(
        command,
        req_id="Б-123456",
        policy=DONE_POLICY,
        messenger=messenger,
        replier=replier,
    )

    assert ready.ok
    assert settlements.ready_calls == [("Б-123456", 77)]
    assert messenger.edits[0].reply_markup == ("done", "Б-123456")
    assert not blocked.ok
    assert done.ok
    assert schedule.removed == ["Б-123456"]
