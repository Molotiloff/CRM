from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class AsyncLifecycle(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class LifecycleComponent:
    name: str
    lifecycle: AsyncLifecycle


@dataclass(frozen=True, slots=True)
class LifecycleFailure:
    component: str
    error: Exception


class LifecycleStartupError(RuntimeError):
    def __init__(
        self,
        component: str,
        error: Exception,
        rollback_failures: tuple[LifecycleFailure, ...],
    ) -> None:
        super().__init__(f"Failed to start lifecycle component {component}: {error}")
        self.component = component
        self.error = error
        self.rollback_failures = rollback_failures


class LifecycleShutdownError(RuntimeError):
    def __init__(self, failures: tuple[LifecycleFailure, ...]) -> None:
        names = ", ".join(failure.component for failure in failures)
        super().__init__(f"Failed to stop lifecycle components: {names}")
        self.failures = failures


class LifecycleSupervisor:
    """Starts components in order and always stops the started subset in reverse."""

    def __init__(self, components: tuple[LifecycleComponent, ...]) -> None:
        self._components = components
        self._started: list[LifecycleComponent] = []
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return bool(self._started)

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            for component in self._components:
                try:
                    await component.lifecycle.start()
                except asyncio.CancelledError:
                    await self._rollback_started()
                    raise
                except Exception as exc:
                    rollback_failures = await self._rollback_started()
                    raise LifecycleStartupError(
                        component.name,
                        exc,
                        rollback_failures,
                    ) from exc
                self._started.append(component)
                log.info("%s started", component.name)

    async def stop(self) -> None:
        async with self._lock:
            stop_task = asyncio.create_task(
                self._stop_started(),
                name="lifecycle_supervisor_shutdown",
            )
            try:
                failures = await asyncio.shield(stop_task)
            except asyncio.CancelledError:
                failures = await stop_task
                self._log_shutdown_failures(failures)
                raise
            if failures:
                raise LifecycleShutdownError(failures)

    async def _stop_started(self) -> tuple[LifecycleFailure, ...]:
        failures: list[LifecycleFailure] = []
        while self._started:
            component = self._started.pop()
            try:
                await component.lifecycle.stop()
            except Exception as exc:
                failures.append(LifecycleFailure(component.name, exc))
                log.exception("%s failed to stop", component.name)
            else:
                log.info("%s stopped", component.name)
        return tuple(failures)

    async def _rollback_started(self) -> tuple[LifecycleFailure, ...]:
        return await asyncio.shield(self._stop_started())

    @staticmethod
    def _log_shutdown_failures(failures: tuple[LifecycleFailure, ...]) -> None:
        for failure in failures:
            log.error(
                "%s failed to stop during cancellation: %s",
                failure.component,
                failure.error,
            )


class ManagedTaskLifecycle(ABC):
    """Template for one-task workers with idempotent serialized lifecycle calls."""

    def __init__(self, *, task_name: str) -> None:
        self._task_name = task_name
        self._task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def is_stopping(self) -> bool:
        return self._stopping

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self.running:
                return
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name=self._task_name)

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            task = self._task
            if task is None:
                return
            self._stopping = True
            task.cancel()
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            finally:
                self._task = None
                await self._after_stop()

    @abstractmethod
    async def _run(self) -> None: ...

    async def _after_stop(self) -> None:
        return None
