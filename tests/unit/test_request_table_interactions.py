from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from services.messaging import CollectingReplier
from services.request_table.delete_interaction_service import (
    RequestTableDeleteInteractionService,
)
from services.request_table.done_interaction_service import (
    RequestTableDoneInteractionService,
)
from services.request_table.message_builder import RequestTableMessageBuilder
from services.request_table.models import RequestTableCallbackCommand
from services.request_table.session_store import RequestTableSessionStore
from services.request_table.table_done_service import TableDonePayload, TableDoneResult
from tests.fakes import FakeMessenger


class KeyboardStub:
    def processing(self) -> str:
        return "processing"


class ExchangeRequestRepoStub:
    def __init__(self) -> None:
        self.marked: tuple[str, bool] | None = None

    async def mark_exchange_request_table_done(
        self,
        *,
        table_req_id: str,
        is_table_done: bool,
    ) -> None:
        self.marked = (table_req_id, is_table_done)


class DoneServiceStub:
    @staticmethod
    def parse_callback_payload(data: str) -> TableDonePayload | None:
        if data != "req:table_done:10":
            return None
        return TableDonePayload(
            req_id=10,
            in_cur="RUB",
            out_cur="USDT",
            in_amt=Decimal("8000"),
            out_amt=Decimal("100"),
            rate=Decimal("80"),
        )

    @staticmethod
    def parse_table_req_id(data: str) -> None:
        return None

    @staticmethod
    async def write_by_payload(
        *,
        payload: TableDonePayload,
        message_dt: datetime | None,
    ) -> TableDoneResult:
        return TableDoneResult(
            sheet_type="Продажа",
            in_cur=payload.in_cur,
            out_cur=payload.out_cur,
            in_amt=payload.in_amt,
            out_amt=payload.out_amt,
            rate=payload.rate,
        )


class SheetsGatewayStub:
    async def delete_rows_by_request_id(self, **kwargs: object) -> dict[str, int]:
        return {"Покупка": 1, "Продажа": 2}

    async def get_service_account_email(self) -> str:
        return "service@example.com"


def command(data: str) -> RequestTableCallbackCommand:
    return RequestTableCallbackCommand(
        chat_id=100,
        message_id=200,
        message_text="Заявка #10",
        message_dt=datetime(2026, 8, 11, tzinfo=UTC),
        callback_data=data,
    )


async def test_done_interaction_uses_messaging_ports() -> None:
    repo = ExchangeRequestRepoStub()
    messenger = FakeMessenger()
    replier = CollectingReplier()
    service = RequestTableDoneInteractionService(
        repo=repo,
        request_chat_ids=[100],
        done_service=DoneServiceStub(),
        session_store=RequestTableSessionStore(),
        message_builder=RequestTableMessageBuilder(),
        sheets_gateway=SheetsGatewayStub(),
        keyboards=KeyboardStub(),
    )

    await service.handle(
        command("req:table_done:10"),
        messenger=messenger,
        replier=replier,
    )

    assert repo.marked == ("10", True)
    assert messenger.edits[0].reply_markup == "processing"
    assert RequestTableMessageBuilder.STATUS_LINE_DONE in messenger.edits[1].text
    assert messenger.edits[-1].reply_markup is None
    assert replier.alerts and "Занесена в таблицу" in replier.alerts[-1]


async def test_delete_interaction_uses_messaging_ports() -> None:
    messenger = FakeMessenger()
    replier = CollectingReplier()
    service = RequestTableDeleteInteractionService(
        request_chat_ids=[100],
        session_store=RequestTableSessionStore(),
        message_builder=RequestTableMessageBuilder(),
        sheets_gateway=SheetsGatewayStub(),
        keyboards=KeyboardStub(),
    )

    await service.handle_yes(
        command("req:table_del:yes:10"),
        messenger=messenger,
        replier=replier,
    )

    assert messenger.edits[0].reply_markup == "processing"
    assert "Удалено из таблиц" in (messenger.edits[1].text or "")
    assert replier.alerts == ["Удалено из таблиц: Покупка=1, Продажа=2"]
