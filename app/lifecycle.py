from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Protocol

from apscheduler.schedulers.asyncio import AsyncIOScheduler

log = logging.getLogger(__name__)


class AsyncServer(Protocol):
    should_exit: bool

    async def serve(self) -> None: ...


class SchedulerLifecycleAdapter:
    def __init__(self, scheduler: AsyncIOScheduler) -> None:
        self._scheduler = scheduler

    async def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)


class AsyncServerLifecycleAdapter:
    """Owns a long-running async server task and bounds graceful shutdown."""

    def __init__(
        self,
        server: AsyncServer,
        *,
        task_name: str,
        shutdown_timeout_seconds: float = 10.0,
    ) -> None:
        self._server = server
        self._task_name = task_name
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        async with self._lock:
            if self.running:
                return
            self._server.should_exit = False
            self._task = asyncio.create_task(
                self._server.serve(),
                name=self._task_name,
            )

    async def stop(self) -> None:
        async with self._lock:
            task = self._task
            if task is None:
                return
            self._server.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=self._shutdown_timeout_seconds)
            except TimeoutError:
                log.warning(
                    "%s did not stop within %.1fs; cancelling",
                    self._task_name,
                    self._shutdown_timeout_seconds,
                )
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            except asyncio.CancelledError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise
            finally:
                self._task = None
