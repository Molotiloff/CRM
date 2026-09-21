from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.dependencies import require_role
from api.models import ApiUser, UserRole
from api.openapi import error_responses
from api.queries import DashboardQueryService
from api.schemas.dashboard import DashboardResponse, DashboardShadowReportDto

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


def get_dashboard_queries(request: Request) -> DashboardQueryService:
    return request.app.state.dashboard_queries


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    responses=error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def dashboard(
    _: ApiUser = Depends(require_role(UserRole.manager)),
    queries: DashboardQueryService = Depends(get_dashboard_queries),
) -> DashboardResponse:
    return await queries.get_dashboard()


@router.get(
    "/dashboard/shadow-reports/{report_id}",
    response_model=DashboardShadowReportDto,
    responses=error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    ),
)
async def dashboard_shadow_report(
    report_id: int,
    _: ApiUser = Depends(require_role(UserRole.accountant)),
    queries: DashboardQueryService = Depends(get_dashboard_queries),
) -> DashboardShadowReportDto:
    report = await queries.get_shadow_report(report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dashboard shadow report {report_id} not found",
        )
    return report


@router.get(
    "/dashboard/rates",
    response_model=dict[str, float],
    responses=error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ),
)
async def dashboard_rates(
    _: ApiUser = Depends(require_role(UserRole.manager)),
    queries: DashboardQueryService = Depends(get_dashboard_queries),
) -> dict[str, float]:
    return await queries.get_rates()
