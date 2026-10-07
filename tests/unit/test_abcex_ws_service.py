from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from services.rate_order.abcex_ws_service import ORDERBOOK_STREAM, AbcexWsService


def frame(seq=67134, kind="snapshot", asks=None, bids=None):
    return json.dumps({
        "stream": ORDERBOOK_STREAM,
        "data": {
            "marketId": "USDTRUB", "seq": seq, "type": kind,
            "asks": asks if asks is not None else [["87.48", "3000"], ["87.46", "92289.3747"]],
            "bids": bids if bids is not None else [["87.42", "125066.2899"], ["87.45", "108764.8514"]],
        },
    })


async def test_har_snapshot_and_additive_deltas():
    notify = AsyncMock()
    service = AbcexWsService(on_orderbook_update=notify)
    await service._handle_message(frame())
    assert service.best_ask == Decimal("87.46")
    assert service.get_best_bid() == Decimal("87.45")
    await service._handle_message(frame(67135, "delta", [], [["87.45", "-50600"]]))
    assert service.get_bids()[0]["volume"] == "58164.8514"
    await service._handle_message(frame(67136, "delta", [["87.48", "-3000"]], []))
    assert len(service.get_asks()) == 1
    await service._handle_message(frame(67137, "delta", [["87.46", "7000"]], []))
    assert service.get_asks()[0]["volume"] == "99289.3747"
    assert service.orderbook_observed_at is not None
    assert notify.await_count == 4
    # Duplicate deltas must never double the volume change.
    await service._handle_message(frame(67137, "delta", [["87.46", "7000"]], []))
    assert notify.await_count == 4


async def test_sequence_gap_requires_new_snapshot():
    service = AbcexWsService()
    await service._handle_message(frame())
    with pytest.raises(ValueError, match="sequence gap"):
        await service._handle_message(frame(67136, "delta", [], []))
    service._reset()
    assert service.get_asks() == service.get_bids() == []
    with pytest.raises(ValueError, match="before snapshot"):
        await service._handle_message(frame(67137, "delta", [], []))
    await service._handle_message(frame(70000))
    assert service.best_ask == Decimal("87.46")


async def test_control_frames_and_other_markets_are_ignored():
    service = AbcexWsService()
    await service._handle_message('{"id":0,"data":["USDTRUB@orderbook"]}\n{"pong":123}\n')
    await service._handle_message(frame().replace("USDTRUB", "USDTUSD"))
    assert service.get_asks() == []


async def test_invalid_delta_does_not_partially_apply():
    service = AbcexWsService()
    await service._handle_message(frame())
    before = service.get_asks()
    with pytest.raises(ValueError):
        await service._handle_message(frame(67135, "delta", [["87.46", "100"]], [["87.45", "-999999"]]))
    assert service.get_asks() == before


async def test_subscription_uses_public_orderbook_stream():
    import asyncio

    ws = AsyncMock()
    ws.recv.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await AbcexWsService()._consume(ws)
    assert json.loads(ws.send.await_args.args[0]) == {
        "method": "subscribe", "data": [ORDERBOOK_STREAM],
    }


async def test_application_heartbeat(monkeypatch):
    import asyncio

    from services.rate_order import abcex_ws_service

    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError])
    monkeypatch.setattr(abcex_ws_service.asyncio, "sleep", sleep)
    ws = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await AbcexWsService()._heartbeat(ws)
    sleep.assert_any_await(10)
    ping = json.loads(ws.send.await_args.args[0])
    assert ping["method"] == "ping"
    assert isinstance(ping["data"], int)
