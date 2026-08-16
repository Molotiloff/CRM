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
from services.cash_requests.deal_status_workflow import CANCEL_POLICY, DONE_POLICY
from services.cash_requests.parsing import ParsedRequest
from services.cash_requests.request_router_service import RequestRouterService


class FakeScheduleService:
    def __init__(self) -> None:
        self.entries: list[ScheduleEntry] = []
        self.removed: list[str] = []

    async def upsert_entry(self, entry: ScheduleEntry) -> None:
        self.entries.append(entry)

    async def remove_entry(self, *, req_id: str) -> bool:
        self.removed.append(req_id)
        return True


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
