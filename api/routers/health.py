from __future__ import annotations

from fastapi import APIRouter, Request

from api.models import HealthResponse, MetricsResponse
from observability import MetricsReader

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/metrics", response_model=MetricsResponse)
async def metrics(request: Request) -> MetricsResponse:
    registry: MetricsReader = request.app.state.metrics
    return MetricsResponse.from_snapshot(registry.snapshot())
