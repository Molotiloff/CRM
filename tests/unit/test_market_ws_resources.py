from __future__ import annotations

import asyncio
import gc
import json
import logging
import weakref

import pytest

from services.rate_order import abcex_ws_service, rapira_ws_service


@pytest.mark.parametrize("module", [abcex_ws_service, rapira_ws_service])
async def test_reconnects_release_connections_and_tasks(monkeypatch, caplog, module):
    # Captured log tracebacks would intentionally retain every fake socket.
    caplog.set_level(logging.CRITICAL, logger=module.log.name)
    original_sleep = asyncio.sleep
    reached = asyncio.Event()
    references = []
    opened = closed = 0
    options = []
    rapira = module is rapira_ws_service
    service = module.RapiraWsService() if rapira else module.AbcexWsService()

    class Socket:
        def __init__(self):
            self.frames = ["0{}", "40"] if rapira else []

        async def send(self, raw):
            pass

        async def recv(self):
            if self.frames:
                return self.frames.pop(0)
            if opened >= 100:
                reached.set()
                await asyncio.Event().wait()
            raise OSError("test disconnect")

    class Connection:
        async def __aenter__(self):
            nonlocal opened
            opened += 1
            self.socket = Socket()
            references.append(weakref.ref(self.socket))
            return self.socket

        async def __aexit__(self, *_args):
            nonlocal closed
            closed += 1
            self.socket = None

    def connect(url, **kwargs):
        options.append(kwargs)
        return Connection()

    async def fast_reconnect(delay):
        await original_sleep(0 if delay == 3 else delay)

    monkeypatch.setattr(module.websockets, "connect", connect)
    monkeypatch.setattr(module.asyncio, "sleep", fast_reconnect)
    await service.start()
    worker = service._task
    await service.start()
    assert service._task is worker
    await asyncio.wait_for(reached.wait(), 5)
    await asyncio.wait_for(service.stop(), 1)
    await original_sleep(0)
    gc.collect()
    assert opened == closed == 100
    assert not any(ref() is not None for ref in references)
    assert worker.done()
    assert not any(
        "_heartbeat" in str(task.get_coro()) for task in asyncio.all_tasks()
    )
    assert service.get_asks() == service.get_bids() == []
    for option in options:
        assert option["max_queue"] == 4
        assert option["compression"] is None
        assert option["ping_interval"] is None


async def test_abcex_book_cannot_grow_without_bound(monkeypatch):
    monkeypatch.setattr(abcex_ws_service, "MAX_BOOK_LEVELS", 1)
    service = abcex_ws_service.AbcexWsService()
    with pytest.raises(ValueError, match="too large"):
        await service._handle_message(json.dumps({
            "stream": abcex_ws_service.ORDERBOOK_STREAM,
            "data": {
                "marketId": "USDTRUB", "type": "snapshot", "seq": 1,
                "asks": [["87", "100"], ["88", "100"]], "bids": [],
            },
        }))
    assert service.get_asks() == []
