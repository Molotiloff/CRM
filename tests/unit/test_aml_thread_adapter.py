from __future__ import annotations

import asyncio
import threading
import time

import pytest

from services.aml import ThreadedAMLChecker
from services.aml.models import AMLCheckRequest


class TrackingSyncAMLChecker:
    def __init__(self) -> None:
        self.thread_id: int | None = None

    def check_wallet(
        self,
        request: AMLCheckRequest,
        *,
        cancellation_event: threading.Event | None = None,
    ) -> dict:
        self.thread_id = threading.get_ident()
        time.sleep(0.01)
        return {"wallet": request.value}


class CooperativeSyncAMLChecker:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()

    def check_wallet(
        self,
        request: AMLCheckRequest,
        *,
        cancellation_event: threading.Event | None = None,
    ) -> dict:
        assert cancellation_event is not None
        self.started.set()
        if cancellation_event.wait(timeout=1):
            self.cancelled.set()
            raise RuntimeError("cancelled")
        return {"wallet": request.value}


async def test_aml_adapter_runs_sync_checker_outside_event_loop() -> None:
    sync_checker = TrackingSyncAMLChecker()
    checker = ThreadedAMLChecker(sync_checker)
    event_loop_thread = threading.get_ident()

    result = await checker.check_wallet(AMLCheckRequest(value="wallet"))

    assert result == {"wallet": "wallet"}
    assert sync_checker.thread_id is not None
    assert sync_checker.thread_id != event_loop_thread


async def test_aml_adapter_cooperatively_cancels_sync_checker() -> None:
    sync_checker = CooperativeSyncAMLChecker()
    checker = ThreadedAMLChecker(sync_checker)
    task = asyncio.create_task(checker.check_wallet(AMLCheckRequest(value="wallet")))
    while not sync_checker.started.is_set():
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sync_checker.cancelled.is_set()
