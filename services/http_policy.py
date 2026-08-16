from __future__ import annotations

from dataclasses import dataclass, replace

import httpx

RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class HttpTimeoutPolicy:
    connect_seconds: float = 10.0
    read_seconds: float = 20.0
    write_seconds: float = 20.0
    pool_seconds: float = 5.0

    def __post_init__(self) -> None:
        if (
            min(
                self.connect_seconds,
                self.read_seconds,
                self.write_seconds,
                self.pool_seconds,
            )
            <= 0
        ):
            raise ValueError("HTTP timeout values must be positive")

    def as_httpx(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_seconds,
            read=self.read_seconds,
            write=self.write_seconds,
            pool=self.pool_seconds,
        )

    def as_requests(self) -> tuple[float, float]:
        return self.connect_seconds, self.read_seconds


@dataclass(frozen=True, slots=True)
class HttpRetryPolicy:
    max_attempts: int = 4
    backoff_base_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    retryable_statuses: frozenset[int] = RETRYABLE_HTTP_STATUSES

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("HTTP retry max_attempts must be positive")
        if self.backoff_base_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("HTTP retry delays cannot be negative")

    def with_max_attempts(self, max_attempts: int) -> HttpRetryPolicy:
        return replace(self, max_attempts=max(1, int(max_attempts)))

    def should_retry(self, *, attempt: int, status_code: int | None = None) -> bool:
        if attempt >= self.max_attempts:
            return False
        return status_code is None or status_code in self.retryable_statuses

    def delay_seconds(self, *, attempt: int, retry_after: str | None = None) -> float:
        server_delay = self._parse_retry_after(retry_after)
        if server_delay is not None:
            return min(server_delay, self.max_delay_seconds)
        exponential = self.backoff_base_seconds * (2 ** max(attempt - 1, 0))
        return min(exponential, self.max_delay_seconds)

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(float(value), 0.0)
        except ValueError:
            return None
