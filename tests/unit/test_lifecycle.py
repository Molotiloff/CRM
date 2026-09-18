from __future__ import annotations

import asyncio

import pytest

from app.lifecycle import AsyncServerLifecycleAdapter, SchedulerLifecycleAdapter
from app.runtime import BotRuntimeServices
from services.aml import AMLQueueService
from services.lifecycle import (
    AsyncLifecycle,
    LifecycleComponent,
    LifecycleShutdownError,
    LifecycleStartupError,
    LifecycleSupervisor,
    ManagedTaskLifecycle,
)
from services.payment_watch import PaymentWatchPoller
from services.rate_order.grinex_ws_service import GrinexWsService
from services.rate_order.rapira_ws_service import RapiraWsService
from services.tg_outbox import TgOutboxWorker


class BlockingWorker(ManagedTaskLifecycle):
    def __init__(self) -> None:
        super().__init__(task_name="blocking_worker")
        self.run_count = 0
        self.stop_count = 0
        self.started = asyncio.Event()

    async def _run(self) -> None:
        self.run_count += 1
        self.started.set()
        await asyncio.Event().wait()

    async def _after_stop(self) -> None:
        self.stop_count += 1


class SchedulerStub:
    def __init__(self) -> None:
        self.running = False
        self.start_count = 0
        self.stop_count = 0

    def start(self) -> None:
        self.running = True
        self.start_count += 1

    def shutdown(self, *, wait: bool) -> None:
        assert wait is False
        self.running = False
        self.stop_count += 1


class RecordingLifecycle:
    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        start_error: Exception | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self._name = name
        self._events = events
        self._start_error = start_error
        self._stop_error = stop_error

    async def start(self) -> None:
        self._events.append(f"start:{self._name}")
        if self._start_error:
            raise self._start_error

    async def stop(self) -> None:
        self._events.append(f"stop:{self._name}")
        if self._stop_error:
            raise self._stop_error


class BlockingStopLifecycle(RecordingLifecycle):
    def __init__(self, name: str, events: list[str]) -> None:
        super().__init__(name, events)
        self.stop_started = asyncio.Event()
        self.release_stop = asyncio.Event()

    async def stop(self) -> None:
        self._events.append(f"stop:{self._name}")
        self.stop_started.set()
        await self.release_stop.wait()


class AsyncServerStub:
    def __init__(self, *, ignore_exit: bool = False) -> None:
        self.should_exit = False
        self.ignore_exit = ignore_exit
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.cancelled = False

    async def serve(self) -> None:
        self.started.set()
        try:
            while self.ignore_exit or not self.should_exit:
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.stopped.set()


async def test_managed_task_lifecycle_is_idempotent_and_restartable() -> None:
    worker = BlockingWorker()

    assert isinstance(worker, AsyncLifecycle)
    await worker.start()
    await worker.start()
    await worker.started.wait()

    assert worker.running is True
    assert worker.run_count == 1

    await worker.stop()
    await worker.stop()

    assert worker.running is False
    assert worker.stop_count == 1

    worker.started.clear()
    await worker.start()
    await worker.started.wait()
    assert worker.run_count == 2
    await worker.stop()


async def test_scheduler_adapter_implements_async_idempotent_lifecycle() -> None:
    scheduler = SchedulerStub()
    lifecycle = SchedulerLifecycleAdapter(scheduler)  # type: ignore[arg-type]

    assert isinstance(lifecycle, AsyncLifecycle)
    await lifecycle.start()
    await lifecycle.start()
    await lifecycle.stop()
    await lifecycle.stop()

    assert scheduler.start_count == 1
    assert scheduler.stop_count == 1


def test_all_task_workers_share_managed_lifecycle_template() -> None:
    worker_types = (
        AMLQueueService,
        GrinexWsService,
        PaymentWatchPoller,
        RapiraWsService,
        TgOutboxWorker,
    )

    assert all(issubclass(worker_type, ManagedTaskLifecycle) for worker_type in worker_types)


async def test_supervisor_starts_in_order_and_stops_in_reverse() -> None:
    events: list[str] = []
    supervisor = LifecycleSupervisor(
        tuple(
            LifecycleComponent(name, RecordingLifecycle(name, events))
            for name in ("first", "second", "third")
        ),
    )

    await supervisor.start()
    await supervisor.start()
    assert supervisor.running is True

    await supervisor.stop()
    await supervisor.stop()

    assert supervisor.running is False
    assert events == [
        "start:first",
        "start:second",
        "start:third",
        "stop:third",
        "stop:second",
        "stop:first",
    ]


async def test_supervisor_rolls_back_started_components_on_start_failure() -> None:
    events: list[str] = []
    failure = RuntimeError("cannot start")
    supervisor = LifecycleSupervisor(
        (
            LifecycleComponent("first", RecordingLifecycle("first", events)),
            LifecycleComponent("second", RecordingLifecycle("second", events)),
            LifecycleComponent(
                "third",
                RecordingLifecycle("third", events, start_error=failure),
            ),
        ),
    )

    with pytest.raises(LifecycleStartupError) as error:
        await supervisor.start()

    assert error.value.component == "third"
    assert error.value.error is failure
    assert error.value.rollback_failures == ()
    assert supervisor.running is False
    assert events == [
        "start:first",
        "start:second",
        "start:third",
        "stop:second",
        "stop:first",
    ]


async def test_supervisor_reports_shutdown_failures_after_stopping_everything() -> None:
    events: list[str] = []
    supervisor = LifecycleSupervisor(
        (
            LifecycleComponent(
                "first",
                RecordingLifecycle("first", events, stop_error=RuntimeError("first")),
            ),
            LifecycleComponent("second", RecordingLifecycle("second", events)),
            LifecycleComponent(
                "third",
                RecordingLifecycle("third", events, stop_error=RuntimeError("third")),
            ),
        ),
    )
    await supervisor.start()

    with pytest.raises(LifecycleShutdownError) as error:
        await supervisor.stop()

    assert [failure.component for failure in error.value.failures] == ["third", "first"]
    assert supervisor.running is False
    assert events[-3:] == ["stop:third", "stop:second", "stop:first"]


async def test_supervisor_finishes_shutdown_when_caller_is_cancelled() -> None:
    events: list[str] = []
    blocking = BlockingStopLifecycle("second", events)
    supervisor = LifecycleSupervisor(
        (
            LifecycleComponent("first", RecordingLifecycle("first", events)),
            LifecycleComponent("second", blocking),
        ),
    )
    await supervisor.start()

    shutdown = asyncio.create_task(supervisor.stop())
    await blocking.stop_started.wait()
    shutdown.cancel()
    await asyncio.sleep(0)

    assert shutdown.done() is False
    blocking.release_stop.set()
    with pytest.raises(asyncio.CancelledError):
        await shutdown

    assert supervisor.running is False
    assert events[-2:] == ["stop:second", "stop:first"]


async def test_async_server_adapter_stops_server_gracefully() -> None:
    server = AsyncServerStub()
    lifecycle = AsyncServerLifecycleAdapter(server, task_name="test-server")

    await lifecycle.start()
    await lifecycle.start()
    await server.started.wait()
    await lifecycle.stop()
    await lifecycle.stop()

    assert server.should_exit is True
    assert server.stopped.is_set()
    assert server.cancelled is False
    assert lifecycle.running is False


async def test_async_server_adapter_cancels_server_after_shutdown_timeout() -> None:
    server = AsyncServerStub(ignore_exit=True)
    lifecycle = AsyncServerLifecycleAdapter(
        server,
        task_name="test-server",
        shutdown_timeout_seconds=0.01,
    )
    await lifecycle.start()
    await server.started.wait()

    await lifecycle.stop()

    assert server.cancelled is True
    assert server.stopped.is_set()
    assert lifecycle.running is False


def test_runtime_supervises_all_background_components() -> None:
    components = [RecordingLifecycle(str(index), []) for index in range(6)]
    runtime = BotRuntimeServices(
        daily_balances_scheduler=components[0],
        best_change_month_report_scheduler=components[1],
        aml_queue_service=components[2],  # type: ignore[arg-type]
        payment_watch_poller=components[3],  # type: ignore[arg-type]
        tg_outbox_worker=components[4],  # type: ignore[arg-type]
        market_ws_service=components[5],  # type: ignore[arg-type]
    )

    assert [component.name for component in runtime._lifecycle_components()] == [
        "Daily balances scheduler",
        "BestChange month report scheduler",
        "AML queue service",
        "Payment watch poller",
        "Telegram outbox worker",
        "Rapira websocket service",
    ]
