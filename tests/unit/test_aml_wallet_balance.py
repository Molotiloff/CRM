from __future__ import annotations

from decimal import Decimal

import pytest

from services.aml.aml_queue_service import AMLQueueService, AMLQueueTask
from services.aml.models import AMLCheckRequest


class Checker:
    async def check_wallet(self, request: AMLCheckRequest) -> dict:
        return {
            "message_text": "original report",
            "report_data": {
                "asset_name": "USDT",
                "hash": request.value,
                "risk_percent": "0%",
                "report_date": "29.09.2026 16:51",
                "preview_link": "https://getblock.net/report",
            },
        }


class BalanceProvider:
    def __init__(self, *, error: bool = False) -> None:
        self.error = error
        self.calls: list[str] = []

    async def get_usdt_balance(self, *, address: str) -> Decimal:
        self.calls.append(address)
        if self.error:
            raise RuntimeError("Tronscan unavailable")
        return Decimal("123.456789")


async def _noop(_value: object) -> None:
    pass


@pytest.mark.parametrize(
    ("aml_request", "should_add_balance"),
    [
        (AMLCheckRequest(value="Taddress"), True),
        (AMLCheckRequest(value="hash", kind="transaction"), False),
        (AMLCheckRequest(value="0xaddress", network="erc20"), False),
    ],
)
async def test_balance_is_added_only_to_trc20_address_report(
    aml_request: AMLCheckRequest, should_add_balance: bool
) -> None:
    provider = BalanceProvider()
    queue = AMLQueueService(checker=Checker(), tron_balance_provider=provider)
    result = await queue._check_wallet(
        AMLQueueTask(request=aml_request, on_success=_noop, on_error=_noop)
    )

    if should_add_balance:
        assert "На балансе кошелька: 123.456789 USDT" in result["message_text"]
        assert provider.calls == [aml_request.value]
        assert result["message_text"].index("На балансе кошелька") < result["message_text"].index("🟢")
    else:
        assert result["message_text"] == "original report"
        assert provider.calls == []


async def test_tronscan_failure_keeps_aml_report() -> None:
    queue = AMLQueueService(checker=Checker(), tron_balance_provider=BalanceProvider(error=True))
    result = await queue._check_wallet(
        AMLQueueTask(request=AMLCheckRequest(value="Taddress"), on_success=_noop, on_error=_noop)
    )

    assert "На балансе кошелька: недоступен (Tronscan)" in result["message_text"]
    assert "Низкий уровень риска" in result["message_text"]
    assert "https://getblock.net/report" in result["message_text"]
