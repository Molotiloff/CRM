from __future__ import annotations

import asyncio
import functools
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import ParamSpec, Protocol, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class DurationMetricSnapshot:
    count: int
    successes: int
    errors: int
    cancellations: int
    total_seconds: float
    max_seconds: float
    last_seconds: float


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    use_cases: Mapping[str, DurationMetricSnapshot]
    queues: Mapping[str, int]


class MetricsRecorder(Protocol):
    def observe_duration(
        self,
        name: str,
        duration_seconds: float,
        *,
        outcome: str,
    ) -> None: ...

    def set_queue_size(self, name: str, size: int) -> None: ...


class MetricsReader(Protocol):
    def snapshot(self) -> MetricsSnapshot: ...


class MetricsPort(MetricsRecorder, MetricsReader, Protocol):
    pass


@dataclass(slots=True)
class _DurationMetric:
    count: int = 0
    successes: int = 0
    errors: int = 0
    cancellations: int = 0
    total_seconds: float = 0.0
    max_seconds: float = 0.0
    last_seconds: float = 0.0

    def observe(self, duration_seconds: float, outcome: str) -> None:
        self.count += 1
        self.total_seconds += duration_seconds
        self.max_seconds = max(self.max_seconds, duration_seconds)
        self.last_seconds = duration_seconds
        if outcome == "success":
            self.successes += 1
        elif outcome == "cancelled":
            self.cancellations += 1
        else:
            self.errors += 1

    def snapshot(self) -> DurationMetricSnapshot:
        return DurationMetricSnapshot(
            count=self.count,
            successes=self.successes,
            errors=self.errors,
            cancellations=self.cancellations,
            total_seconds=self.total_seconds,
            max_seconds=self.max_seconds,
            last_seconds=self.last_seconds,
        )


class InMemoryMetrics:
    """Bounded process metrics registry.

    It stores one aggregate per stable metric name and never retains individual
    observations or business identifiers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._durations: dict[str, _DurationMetric] = {}
        self._queues: dict[str, int] = {}

    def observe_duration(
        self,
        name: str,
        duration_seconds: float,
        *,
        outcome: str,
    ) -> None:
        duration = max(0.0, float(duration_seconds))
        with self._lock:
            self._durations.setdefault(name, _DurationMetric()).observe(duration, outcome)

    def set_queue_size(self, name: str, size: int) -> None:
        with self._lock:
            self._queues[name] = max(0, int(size))

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            return MetricsSnapshot(
                use_cases={
                    name: metric.snapshot()
                    for name, metric in sorted(self._durations.items())
                },
                queues=dict(sorted(self._queues.items())),
            )


class NullMetrics:
    def observe_duration(
        self,
        name: str,
        duration_seconds: float,
        *,
        outcome: str,
    ) -> None:
        return None

    def set_queue_size(self, name: str, size: int) -> None:
        return None

    def snapshot(self) -> MetricsSnapshot:
        return MetricsSnapshot(use_cases={}, queues={})


NULL_METRICS: MetricsPort = NullMetrics()


def measured_operation(
    name: str,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Measure an async method whose owner exposes an injected ``_metrics`` port."""

    def decorator(function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(function)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            owner = args[0] if args else None
            metrics: MetricsRecorder = getattr(owner, "_metrics", NULL_METRICS)
            started = time.perf_counter()
            outcome = "success"
            try:
                return await function(*args, **kwargs)
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception:
                outcome = "error"
                raise
            finally:
                metrics.observe_duration(
                    name,
                    time.perf_counter() - started,
                    outcome=outcome,
                )

        return wrapper

    return decorator
