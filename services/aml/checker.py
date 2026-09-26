from __future__ import annotations

import threading
from typing import Any, Protocol

from services.aml.models import AMLCheckRequest
from services.thread_adapter import SerializedThreadExecutor

AMLCheckResult = dict[str, Any]


class SyncAMLChecker(Protocol):
    def check_wallet(
        self,
        request: AMLCheckRequest,
        *,
        cancellation_event: threading.Event | None = None,
    ) -> AMLCheckResult: ...


class AsyncAMLChecker(Protocol):
    async def check_wallet(self, request: AMLCheckRequest) -> AMLCheckResult: ...


class ThreadedAMLChecker:
    def __init__(self, checker: SyncAMLChecker) -> None:
        self._checker = checker
        self._executor = SerializedThreadExecutor(task_name="aml_check_io")

    async def check_wallet(self, request: AMLCheckRequest) -> AMLCheckResult:
        cancellation_event = threading.Event()
        return await self._executor.run(
            self._checker.check_wallet,
            request,
            cancellation_event=cancellation_event,
            on_cancel=cancellation_event.set,
        )
