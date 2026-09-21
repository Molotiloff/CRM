from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import get_current_user
from api.models import ApiUser, UserRole
from api.routers import health
from observability import InMemoryMetrics, measured_operation
from services.aml import AMLQueueService
from services.aml.aml_queue_service import AMLQueueTask


class MeasuredService:
    def __init__(self, metrics: InMemoryMetrics) -> None:
        self._metrics = metrics

    @measured_operation("example.run")
    async def run(self, *, error: BaseException | None = None) -> str:
        if error is not None:
            raise error
        return "ok"


async def test_measured_operation_aggregates_outcomes_without_raw_samples() -> None:
    metrics = InMemoryMetrics()
    service = MeasuredService(metrics)

    assert await service.run() == "ok"
    with pytest.raises(RuntimeError, match="failed"):
        await service.run(error=RuntimeError("failed"))
    with pytest.raises(asyncio.CancelledError):
        await service.run(error=asyncio.CancelledError())

    metric = metrics.snapshot().use_cases["example.run"]
    assert metric.count == 3
    assert metric.successes == 1
    assert metric.errors == 1
    assert metric.cancellations == 1
    assert metric.total_seconds >= metric.max_seconds >= metric.last_seconds >= 0


def test_metrics_endpoint_returns_duration_aggregates_and_queue_gauges() -> None:
    metrics = InMemoryMetrics()
    metrics.observe_duration("deal.list", 0.25, outcome="success")
    metrics.set_queue_size("tg_outbox.pending", 3)
    app = FastAPI()
    app.state.metrics = metrics
    app.dependency_overrides[get_current_user] = _admin_user
    app.include_router(health.router)

    response = TestClient(app).get("/api/v1/metrics")

    assert response.status_code == 200
    assert response.json() == {
        "use_cases": {
            "deal.list": {
                "count": 1,
                "successes": 1,
                "errors": 0,
                "cancellations": 0,
                "total_seconds": 0.25,
                "max_seconds": 0.25,
                "last_seconds": 0.25,
            }
        },
        "queues": {"tg_outbox.pending": 3},
    }


def test_metrics_endpoint_requires_authentication() -> None:
    app = FastAPI()
    app.state.metrics = InMemoryMetrics()
    app.state.config = SimpleNamespace(api_dev_auth_bypass=False)
    app.state.user_repository = object()
    app.include_router(health.router)

    response = TestClient(app).get("/api/v1/metrics")

    assert response.status_code == 401


def test_metrics_endpoint_requires_admin_role() -> None:
    app = FastAPI()
    app.state.metrics = InMemoryMetrics()
    app.dependency_overrides[get_current_user] = _accountant_user
    app.include_router(health.router)

    response = TestClient(app).get("/api/v1/metrics")

    assert response.status_code == 403


def _admin_user() -> ApiUser:
    return ApiUser(
        id=1,
        tg_user_id=42,
        display_name="Admin",
        role=UserRole.admin,
        cities=[],
        is_active=True,
    )


def _accountant_user() -> ApiUser:
    return ApiUser(
        id=2,
        tg_user_id=43,
        display_name="Accountant",
        role=UserRole.accountant,
        cities=[],
        is_active=True,
    )


async def test_aml_queue_publishes_pending_size_on_enqueue() -> None:
    class Checker:
        async def check_wallet(self, wallet: str) -> dict[str, object]:
            return {"wallet": wallet}

    metrics = InMemoryMetrics()
    queue = AMLQueueService(checker=Checker(), metrics=metrics)

    async def on_success(_: dict[str, object]) -> None:
        return None

    async def on_error(_: Exception) -> None:
        return None

    await queue.enqueue(
        AMLQueueTask(
            wallet="wallet",
            on_success=on_success,
            on_error=on_error,
        )
    )

    assert metrics.snapshot().queues["aml.pending"] == 1
