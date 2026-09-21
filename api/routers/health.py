from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from api.dependencies import require_role
from api.models import ApiUser, HealthResponse, MetricsResponse, UserRole
from api.openapi import error_responses
from observability import MetricsReader

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    responses=error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ),
)
async def metrics(
    request: Request,
    _: ApiUser = Depends(require_role(UserRole.admin)),
) -> MetricsResponse:
    registry: MetricsReader = request.app.state.metrics
    return MetricsResponse.from_snapshot(registry.snapshot())
