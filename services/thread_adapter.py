from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")


class SerializedThreadExecutor:
    """Runs blocking calls serially without releasing their lock on cancellation."""

    def __init__(self, *, task_name: str) -> None:
        self._task_name = task_name
        self._lock = asyncio.Lock()

    async def run(
        self,
        function: Callable[..., T],
        /,
        *args: object,
        on_cancel: Callable[[], None] | None = None,
        **kwargs: object,
    ) -> T:
        async with self._lock:
            task = asyncio.create_task(
                asyncio.to_thread(function, *args, **kwargs),
                name=self._task_name,
            )
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if on_cancel is not None:
                    on_cancel()
                try:
                    await task
                except Exception:  # noqa: BLE001 - cancellation must remain the surfaced error
                    log.info(
                        "%s stopped after async caller cancellation",
                        self._task_name,
                    )
                raise
