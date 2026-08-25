from __future__ import annotations

from decimal import Decimal

import pytest

from domain import Deal, DealEventPayload, DealStatus
from services.crm.deal_service import DealValidationError
from services.crm.deal_status_policy import DealStatusPolicy


@pytest.mark.asyncio
async def test_fixed_uses_exchange_rate_and_rejects_skipped_transition() -> None:
    policy = DealStatusPolicy(FakeWorkflowRepository())
    deal = _deal()

    prepared = await policy.prepare(
        deal,
        new_status=DealStatus.FIXED,
        payload=DealEventPayload({}),
    )

    assert prepared.payload.get("rate") == "95.5"
    assert prepared.body_patch.get("fixed_rate") == "95.5"
    with pytest.raises(DealValidationError, match="new -> balance_check"):
        await policy.prepare(
            deal,
            new_status=DealStatus.BALANCE_CHECK,
            payload=DealEventPayload({}),
        )


@pytest.mark.asyncio
async def test_balance_check_marks_shortage_and_blocks_payment() -> None:
    repository = FakeWorkflowRepository(insufficient=True)
    policy = DealStatusPolicy(repository)
    fixed = _deal(status="fixed")

    checked = await policy.prepare(
        fixed,
        new_status=DealStatus.BALANCE_CHECK,
        payload=DealEventPayload({}),
    )

    assert checked.body_patch.get("insufficient_usdt") is True
    assert checked.payload.get("shortageUsdt") == "25"
    balance_deal = _deal(
        status="balance_check",
        body={**fixed.body.to_dict(), **checked.body_patch.to_dict()},
    )
    with pytest.raises(DealValidationError, match="insufficient"):
        await policy.prepare(
            balance_deal,
            new_status=DealStatus.AWAITING_PAYMENT,
            payload=DealEventPayload({}),
        )


@pytest.mark.asyncio
async def test_payment_watch_binding_and_done_tronscan() -> None:
    repository = FakeWorkflowRepository()
    policy = DealStatusPolicy(repository)
    balance_deal = _deal(
        status="balance_check",
        body={**_deal().body.to_dict(), "insufficient_usdt": False},
    )

    awaiting = await policy.prepare(
        balance_deal,
        new_status=DealStatus.AWAITING_PAYMENT,
        payload=DealEventPayload({"paymentWatchId": 12}),
    )
    done = await policy.prepare(
        _deal(status="awaiting_payment"),
        new_status=DealStatus.DONE,
        payload=DealEventPayload({}),
    )

    assert awaiting.payment_watch_id == 12
    assert awaiting.body_patch.get("payment_watch_id") == 12
    assert done.payload.get("txHash") == "abc123"
    assert done.tronscan_url == "https://tronscan.org/#/transaction/abc123"


@pytest.mark.asyncio
async def test_delivery_can_finish_without_payment_watch() -> None:
    policy = DealStatusPolicy(FakeWorkflowRepository())
    deal = _deal(
        deal_id=2,
        status="in_delivery",
        source_kind="cash",
        body={"req_id": "Б-1"},
    )

    prepared = await policy.prepare(
        deal,
        new_status=DealStatus.DONE,
        payload=DealEventPayload({}),
    )

    assert prepared.payment_watch_id is None
    assert prepared.tronscan_url is None


class FakeWorkflowRepository:
    def __init__(self, *, insufficient: bool = False) -> None:
        self.insufficient = insufficient

    async def get_fulfillment_balance_check(self, deal_id: int):
        return {
            "request_chat_id": -777001,
            "usdt_fact": Decimal("75" if self.insufficient else "125"),
            "queued_qty": Decimal("100"),
            "required_amount": Decimal("100"),
            "shortage_amount": Decimal("25" if self.insufficient else "0"),
            "onchain_liquid_qty": Decimal("0" if self.insufficient else "25"),
            "insufficient": self.insufficient,
        }

    async def resolve_payment_watch(self, *, deal_id: int, requested_watch_id: int | None):
        return {"id": requested_watch_id or 12, "status": "WATCHING"}

    async def get_completed_payment(self, deal_id: int):
        return {
            "watch_id": 12,
            "tx_hash": "abc123",
            "amount": Decimal("100"),
            "token_symbol": "USDT",
        }


def _deal(
    *,
    deal_id: int = 1,
    status: str = "new",
    source_kind: str = "exchange",
    body: dict | None = None,
) -> Deal:
    return Deal.from_record({
        "id": deal_id,
        "deal_no": 100000 + deal_id,
        "deal_type": "conversion",
        "city": "екб",
        "status": status,
        "source": "tg_bot",
        "source_kind": source_kind,
        "body": body
        or {
            "recv_code": "RUB",
            "pay_code": "USDT",
            "rate": "95.5",
        },
    })
