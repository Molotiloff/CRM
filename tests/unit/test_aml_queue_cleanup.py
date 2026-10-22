from __future__ import annotations

import asyncio
import gc
import weakref

from services.aml import AMLQueueService, AMLQueueTask
from services.aml.models import AMLCheckRequest


class Result(dict):
    pass


async def test_idle_worker_does_not_hold_last_report_and_callbacks():
    refs = []

    class Checker:
        async def check_wallet(self, request):
            result = Result(message_text="report", payload=bytearray(1024 * 1024))
            refs.append(weakref.ref(result))
            return result

    class Callback:
        async def __call__(self, value):
            pass

    queue = AMLQueueService(checker=Checker())
    callback = Callback()
    callback_ref = weakref.ref(callback)
    await queue.enqueue(AMLQueueTask(AMLCheckRequest("wallet"), callback, callback))
    del callback
    await queue.start()
    try:
        await asyncio.wait_for(queue._queue.join(), timeout=3)
        gc.collect()
        assert all(ref() is None for ref in refs)
        assert callback_ref() is None
    finally:
        await queue.stop()


async def test_failed_error_notification_does_not_kill_queue():
    calls = []

    class Checker:
        async def check_wallet(self, request):
            calls.append(request.value)
            raise RuntimeError("provider failed")

    async def broken(_):
        raise RuntimeError("Telegram failed")

    queue = AMLQueueService(checker=Checker())
    for wallet in ("first", "second"):
        await queue.enqueue(AMLQueueTask(AMLCheckRequest(wallet), broken, broken))
    await queue.start()
    try:
        await asyncio.wait_for(queue._queue.join(), timeout=3)
        assert calls == ["first", "second"]
        assert queue.running
    finally:
        await queue.stop()


async def test_shutdown_discards_queued_callbacks_even_if_never_started():
    queue = AMLQueueService(checker=object())

    class Callback:
        async def __call__(self, value):
            pass

    callback = Callback()
    ref = weakref.ref(callback)
    await queue.enqueue(AMLQueueTask(AMLCheckRequest("wallet"), callback, callback))
    del callback
    await queue.stop()
    assert queue.qsize() == 0
    assert ref() is None
    await asyncio.wait_for(queue._queue.join(), timeout=1)


async def test_shutdown_cancels_active_delivery_and_releases_waiting_tasks():
    started = asyncio.Event()
    finished = asyncio.Event()

    class Checker:
        async def check_wallet(self, request):
            return {"message_text": "report"}

    async def delivery(_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    queue = AMLQueueService(checker=Checker())
    await queue.enqueue(AMLQueueTask(AMLCheckRequest("active"), delivery, delivery))
    await queue.enqueue(AMLQueueTask(AMLCheckRequest("waiting"), delivery, delivery))
    await queue.start()
    await asyncio.wait_for(started.wait(), timeout=3)
    await asyncio.wait_for(queue.stop(), timeout=3)
    assert finished.is_set()
    assert not queue.running
    assert queue.qsize() == 0
    await asyncio.wait_for(queue._queue.join(), timeout=1)
