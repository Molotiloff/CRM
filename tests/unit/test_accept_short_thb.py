from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.exchange.accept_short_service import AcceptShortCommand, AcceptShortService
from services.messaging import CollectingReplier
from tests.fakes import FakeMessenger


def _service() -> AcceptShortService:
    repo = SimpleNamespace(
        ensure_client=AsyncMock(return_value=42),
        snapshot_wallet=AsyncMock(
            return_value=[
                {"currency_code": code, "precision": 2}
                for code in ("RUB", "THB", "USDT", "USD", "EUR", "USDW")
            ]
        ),
    )
    return AcceptShortService(
        repo=repo,
        unit_of_work_factory=MagicMock(),
        source_links=MagicMock(),
        keyboards=MagicMock(),
    )


@pytest.mark.parametrize(
    ("text", "recv_code", "recv_amount", "pay_code", "pay_amount", "rate"),
    [
        (
            "/пр 1000 обат 1000/2.7",
            "RUB", Decimal("1000"), "THB", Decimal("370.37"), "2.7",
        ),
        (
            "/пбат 1000 ор 1000*2.7",
            "THB", Decimal("1000"), "RUB", Decimal("2700"), "2.7",
        ),
        (
            "/пд 10 обат 10*35",
            "USD", Decimal("10"), "THB", Decimal("350"), "35",
        ),
        (
            "/пбат 1000 от 1000/35",
            "THB", Decimal("1000"), "USDT", Decimal("28.57"), "0.02857143",
        ),
    ],
)
async def test_short_exchange_accepts_thb_with_other_currencies(
    text: str,
    recv_code: str,
    recv_amount: Decimal,
    pay_code: str,
    pay_amount: Decimal,
    rate: str,
) -> None:
    parsed = await _service().parse_command(text, chat_id=-100, chat_name="Клиент")

    assert parsed is not None
    assert (parsed.recv_code, parsed.recv_amount) == (recv_code, recv_amount)
    assert (parsed.pay_code, parsed.pay_amount) == (pay_code, pay_amount)
    assert parsed.rate_str == rate


async def test_short_exchange_passes_thb_to_request_creation() -> None:
    service = _service()
    service.create_exchange_request.execute_core = AsyncMock()

    await service.execute_command(
        AcceptShortCommand(
            chat_id=-100,
            chat_name="Клиент",
            message_id=10,
            text="/пр 1000 обат 1000/2.7",
            actor_name="Менеджер",
        ),
        messenger=FakeMessenger(),
        replier=CollectingReplier(),
    )

    params = service.create_exchange_request.execute_core.await_args.args[0]
    assert params.recv_code == "RUB"
    assert params.pay_code == "THB"
    assert params.pay_amount_expr == "1000/2.7"
