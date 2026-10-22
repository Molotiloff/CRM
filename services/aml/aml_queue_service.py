from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from observability import NULL_METRICS, MetricsRecorder, measured_operation
from services.aml.checker import AMLCheckResult, AsyncAMLChecker
from services.aml.getblock_parser import build_report_message
from services.aml.models import AMLCheckRequest
from services.lifecycle import ManagedTaskLifecycle
from services.tron_balance_port import TronBalanceProvider as TronBalanceProvider

log = logging.getLogger("aml_queue")


class AMLQueueFullError(RuntimeError):
    pass


@dataclass(slots=True)
class AMLQueueTask:
    request: AMLCheckRequest
    on_success: Callable[[AMLCheckResult], Awaitable[None]]
    on_error: Callable[[Exception], Awaitable[None]]


class AMLQueueService(ManagedTaskLifecycle):
    def __init__(
        self,
        *,
        checker: AsyncAMLChecker,
        tron_balance_provider: TronBalanceProvider | None = None,
        metrics: MetricsRecorder = NULL_METRICS,
        max_queue_size: int = 20,
    ) -> None:
        super().__init__(task_name="aml_queue_worker")
        self._checker = checker
        self._tron_balance_provider = tron_balance_provider
        self._queue: asyncio.Queue[AMLQueueTask] = asyncio.Queue(
            maxsize=max(1, max_queue_size)
        )
        self._metrics = metrics
        self._metrics.set_queue_size("aml.pending", 0)

    async def enqueue(self, task: AMLQueueTask) -> int:
        try:
            self._queue.put_nowait(task)
        except asyncio.QueueFull as exc:
            raise AMLQueueFullError("Очередь AML-проверок заполнена") from exc
        size = self._queue.qsize()
        self._metrics.set_queue_size("aml.pending", size)
        return size

    def qsize(self) -> int:
        return self._queue.qsize()

    async def _run(self) -> None:
        log.info("AML queue worker started")
        try:
            while not self.is_stopping:
                task = await self._queue.get()
                result = None
                try:
                    result = await self._check_wallet(task)
                    await task.on_success(result)
                except Exception as e:  # noqa: BLE001 — worker reports task failures via callback
                    log.warning("AML task failed target=%s err=%r", task.request.value, e)
                    try:
                        await task.on_error(e)
                    except Exception:
                        log.exception("Failed to notify about AML task failure")
                finally:
                    self._queue.task_done()
                    self._metrics.set_queue_size("aml.pending", self._queue.qsize())
                    # Do not retain the previous report/callbacks while awaiting the next task.
                    result = None
                    del task
        finally:
            log.info("AML queue worker stopped")

    async def stop(self) -> None:
        try:
            await super().stop()
        finally:
            while not self._queue.empty():
                self._queue.get_nowait()
                self._queue.task_done()
            self._metrics.set_queue_size("aml.pending", 0)

    @measured_operation("aml.check_wallet")
    async def _check_wallet(self, task: AMLQueueTask) -> AMLCheckResult:
        result = await self._checker.check_wallet(task.request)
        if (
            task.request.network == "trc20"
            and task.request.kind == "address"
            and isinstance(result.get("report_data"), dict)
        ):
            balance_text = "недоступен (Tronscan)"
            try:
                if self._tron_balance_provider is not None:
                    balance = await self._tron_balance_provider.get_usdt_balance(
                        address=task.request.value
                    )
                    balance_text = f"{balance:.6f} USDT"
            except Exception:  # noqa: BLE001 — отсутствие остатка не отменяет готовый AML-отчёт
                log.warning("Failed to get Tronscan USDT balance for %s", task.request.value, exc_info=True)
                balance_text = "недоступен (Tronscan)"
            result["message_text"] = build_report_message(
                result["report_data"], wallet_balance=balance_text
            )
            result["wallet_balance"] = balance_text
        return result
