from datetime import datetime
from decimal import Decimal

import pytest

from domain import DomainStateError, TelegramMessageRef
from services.exchange.notification_builder import (
    CancelledBalanceLeg,
    ExchangeNotificationBuilder,
)
from services.exchange.request_context import ExchangeRequestContext
from services.exchange.source_link_service import ExchangeSourceLinkService
from services.exchange.wallet_presenter import ExchangeWalletPresenter
from services.exchange.workflow_policy import (
    is_request_chat,
    tracked_exchange_currencies,
)


class FakeExchangeRequestRepository:
    def __init__(self) -> None:
        self.record = None
        self.upserts: list[dict] = []

    async def get_exchange_request_link(self, *, client_req_id: str):
        return self.record

    async def upsert_exchange_request_link(self, **kwargs) -> None:
        self.upserts.append(kwargs)


def test_exchange_chat_policy_is_pure_and_tracks_only_request_chat() -> None:
    assert is_request_chat(-777, -777) is True
    assert is_request_chat(-100, -777) is False
    assert tracked_exchange_currencies(-777, -777) == frozenset({"USDT"})
    assert tracked_exchange_currencies(-100, -777) is None


def test_exchange_request_context_maps_message_and_rejects_partial_reference() -> None:
    context = ExchangeRequestContext.from_record(
        {
            "table_req_id": 101,
            "request_chat_id": -777,
            "request_message_id": 55,
            "request_text": "card",
            "is_table_done": True,
        }
    )

    assert context.table_request_id == "101"
    assert context.request_message == TelegramMessageRef(-777, 55)
    assert context.table_done is True

    with pytest.raises(DomainStateError, match="Incomplete"):
        ExchangeRequestContext.from_record({"request_chat_id": -777})


async def test_source_link_service_persists_only_message_binding() -> None:
    repository = FakeExchangeRequestRepository()
    service = ExchangeSourceLinkService(repository)

    await service.bind_request_card(
        request_id="51374723",
        table_request_id="101",
        message=TelegramMessageRef(-777, 55),
        request_text="card",
    )

    assert repository.upserts == [
        {
            "client_req_id": "51374723",
            "table_req_id": "101",
            "request_chat_id": -777,
            "request_message_id": 55,
            "request_text": "card",
        }
    ]


def test_exchange_notification_builder_formats_cancel_without_side_effects() -> None:
    builder = ExchangeNotificationBuilder()
    cancelled = builder.cancelled_client_card("card", cancelled_at=datetime(2026, 8, 11, 10, 30))
    summary = builder.cancellation_summary(
        "<request>",
        [{"currency_code": "USDT", "balance": "50", "precision": 2}],
        [CancelledBalanceLeg("USDT", Decimal("100"), 2, "-")],
    )

    assert "2026-08-11 10:30" in cancelled
    assert "&lt;request&gt;" in summary
    assert "-100.00 usdt" in summary
    assert builder.cancelled_request_card("card").startswith("⛔️")


def test_wallet_presenter_escapes_client_data() -> None:
    result = ExchangeWalletPresenter.summary(
        "<Client>",
        [{"currency_code": "USDT", "balance": Decimal("10"), "precision": 2}],
    )

    assert "&lt;Client&gt;" in result
    assert "10.00 usdt" in result
