from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from api.dependencies import get_current_user
from api.models import ApiUser
from api.openapi import error_responses
from api.queries import BalanceQueryService
from api.schemas.balances import BalancesSnapshotResponse

router = APIRouter(prefix="/api/v1", tags=["balances"])


def get_balance_queries(request: Request) -> BalanceQueryService:
    return request.app.state.balance_queries


@router.get(
    "/balances",
    response_model=BalancesSnapshotResponse,
    responses=error_responses(status.HTTP_401_UNAUTHORIZED),
)
async def list_balances(
    currency: str | None = None,
    sign: str | None = None,
    _: ApiUser = Depends(get_current_user),
    queries: BalanceQueryService = Depends(get_balance_queries),
) -> BalancesSnapshotResponse:
    return await queries.get_snapshot(currency=currency, sign=sign)
