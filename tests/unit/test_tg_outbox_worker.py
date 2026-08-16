from __future__ import annotations

from datetime import UTC, datetime

import pytest

from db_asyncpg.repositories.tg_outbox import TgOutboxItem
from observability import InMemoryMetrics, current_log_context
from services.tg_outbox import TgOutboxWorker


@pytest.mark.asyncio
async def test_worker_marks_successful_item_sent() -> None:
    repository = FakeOutboxRepository([_item()])
    delivery = FakeDeliveryService()
    metrics = InMemoryMetrics()
    worker = TgOutboxWorker(
        repository=repository,
        delivery_service=delivery,
        metrics=metrics,
    )

    assert await worker.process_once() == 1
    assert repository.sent == [1]
    assert repository.failed == []
    assert delivery.deal_ids == ["7"]
    snapshot = metrics.snapshot()
    assert snapshot.queues["tg_outbox.pending"] == 0
    assert snapshot.use_cases["tg_outbox.process_batch"].successes == 1


@pytest.mark.asyncio
async def test_worker_retries_delivery_error() -> None:
    repository = FakeOutboxRepository([_item(attempts=1)])
    worker = TgOutboxWorker(
        repository=repository,
        delivery_service=FakeDeliveryService(error=RuntimeError("network")),
        max_attempts=3,
    )

    assert await worker.process_once() == 1
    assert repository.sent == []
    assert repository.failed[0][1] is True
    assert "network" in repository.failed[0][2]


class FakeOutboxRepository:
    def __init__(self, items: list[TgOutboxItem]) -> None:
        self.items = items
        self.sent: list[int] = []
        self.failed: list[tuple[int, bool, str]] = []

    async def claim_batch(self, **kwargs):
        items, self.items = self.items, []
        return items

    async def mark_sent(self, outbox_id: int) -> None:
        self.sent.append(outbox_id)

    async def mark_failed(
        self,
        outbox_id: int,
        *,
        error: str,
        retry: bool,
        retry_delay_seconds: int,
    ) -> None:
        self.failed.append((outbox_id, retry, error))

    async def count_pending(self, *, max_attempts: int) -> int:
        return sum(item.attempts < max_attempts for item in self.items)


class FakeDeliveryService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.deal_ids: list[str | None] = []

    async def deliver(self, item: TgOutboxItem) -> None:
        self.deal_ids.append(current_log_context().deal_id)
        if self.error:
            raise self.error


def _item(*, attempts: int = 1) -> TgOutboxItem:
    return TgOutboxItem(
        id=1,
        kind="deal_status_changed",
        payload={"dealId": 7, "newStatus": "fixed"},
        attempts=attempts,
        created_at=datetime.now(UTC),
    )
