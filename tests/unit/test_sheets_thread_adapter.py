from __future__ import annotations

import asyncio
import threading
import time
from decimal import Decimal

import pytest

from gutils.requests_sheet_gateway import ThreadedSheetsTradeGateway


class TrackingSyncSheetsGateway:
    def __init__(self) -> None:
        self.active_calls = 0
        self.max_active_calls = 0
        self.thread_ids: list[int] = []

    def read_main_rate(self, code: str, cell_map=None) -> Decimal:
        self.thread_ids.append(threading.get_ident())
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            time.sleep(0.01)
            return Decimal(code)
        finally:
            self.active_calls -= 1


class BlockingSyncSheetsGateway:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.active_calls = 0
        self.max_active_calls = 0

    def read_main_rate(self, code: str, cell_map=None) -> Decimal:
        self.calls += 1
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        self.started.set()
        try:
            self.release.wait(timeout=1)
            return Decimal(code)
        finally:
            self.active_calls -= 1


async def test_thread_adapter_runs_off_loop_and_serializes_google_sdk() -> None:
    sync_gateway = TrackingSyncSheetsGateway()
    gateway = ThreadedSheetsTradeGateway(sync_gateway)  # type: ignore[arg-type]
    event_loop_thread = threading.get_ident()

    results = await asyncio.gather(
        gateway.read_main_rate("1"),
        gateway.read_main_rate("2"),
    )

    assert results == [Decimal("1"), Decimal("2")]
    assert sync_gateway.max_active_calls == 1
    assert sync_gateway.thread_ids
    assert all(thread_id != event_loop_thread for thread_id in sync_gateway.thread_ids)


async def test_thread_adapter_keeps_lock_until_cancelled_call_finishes() -> None:
    sync_gateway = BlockingSyncSheetsGateway()
    gateway = ThreadedSheetsTradeGateway(sync_gateway)  # type: ignore[arg-type]
    first = asyncio.create_task(gateway.read_main_rate("1"))
    while not sync_gateway.started.is_set():
        await asyncio.sleep(0)

    first.cancel()
    second = asyncio.create_task(gateway.read_main_rate("2"))
    await asyncio.sleep(0.01)

    assert sync_gateway.calls == 1
    assert first.done() is False
    sync_gateway.release.set()

    with pytest.raises(asyncio.CancelledError):
        await first
    assert await second == Decimal("2")
    assert sync_gateway.max_active_calls == 1
