from fastapi import APIRouter, Depends, Query, Request, status

from api.dependencies import require_role
from api.models import ApiUser, UserRole
from api.openapi import error_responses
from api.schemas.manual_cash import (
    ManualCashAccountDto,
    ManualCashMoveDto,
    ManualCashMoveRequest,
    ManualCashReversalRequest,
    ManualCashSnapshotResponse,
)
from services.accounting import (
    ManualCashMove,
    ManualCashService,
    RecordManualCashMove,
    ReverseManualCashMove,
)

router = APIRouter(prefix="/api/v1/dashboard/manual-cash", tags=["dashboard"])


def get_service(request: Request) -> ManualCashService:
    return request.app.state.manual_cash_service


@router.get(
    "",
    response_model=ManualCashSnapshotResponse,
    responses=error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ),
)
async def snapshot(
    limit: int = Query(20, ge=1, le=100),
    _: ApiUser = Depends(require_role(UserRole.accountant)),
    service: ManualCashService = Depends(get_service),
) -> ManualCashSnapshotResponse:
    accounts, moves = await service.snapshot(limit=limit)
    return ManualCashSnapshotResponse(
        accounts=[
            ManualCashAccountDto(code=item.code, name=item.name, balance=str(item.balance))
            for item in accounts
        ],
        moves=[_move(item) for item in moves],
    )


@router.post(
    "/moves",
    response_model=ManualCashMoveDto,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_409_CONFLICT,
    ),
)
async def record(
    payload: ManualCashMoveRequest,
    user: ApiUser = Depends(require_role(UserRole.accountant)),
    service: ManualCashService = Depends(get_service),
) -> ManualCashMoveDto:
    return _move(
        await service.record(
            RecordManualCashMove(
                account_code=payload.accountCode,
                operation=payload.operation,
                amount=payload.amount,
                effective_at=payload.effectiveAt,
                comment=payload.comment,
                actor_user_id=user.id,
                idempotency_key=payload.idempotencyKey,
            )
        )
    )


@router.post(
    "/moves/{move_id}/reverse",
    response_model=ManualCashMoveDto,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_409_CONFLICT,
    ),
)
async def reverse(
    move_id: int,
    payload: ManualCashReversalRequest,
    user: ApiUser = Depends(require_role(UserRole.accountant)),
    service: ManualCashService = Depends(get_service),
) -> ManualCashMoveDto:
    return _move(
        await service.reverse(
            ReverseManualCashMove(
                move_id=move_id,
                comment=payload.comment,
                actor_user_id=user.id,
                idempotency_key=payload.idempotencyKey,
            )
        )
    )


def _move(item: ManualCashMove) -> ManualCashMoveDto:
    return ManualCashMoveDto(
        id=item.id,
        accountCode=item.account_code,
        operation=item.operation,
        amount=str(item.amount),
        balanceAfter=str(item.balance_after),
        effectiveAt=item.effective_at,
        comment=item.comment,
        actorName=item.actor_name,
        reversalOfId=item.reversal_of_id,
        reversed=item.reversed,
    )
