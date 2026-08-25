from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from api.dependencies import require_role
from api.models import ApiUser, UserRole
from api.openapi import error_responses
from api.schemas.fulfillments import (
    FulfillmentCancelRequest,
    FulfillmentQueueItemResponse,
    FulfillmentQueueResponse,
    FulfillmentReorderRequest,
)
from services.accounting.fulfillment_models import ReorderFulfillment
from services.accounting.fulfillment_service import FulfillmentQueueService

router = APIRouter(prefix="/api/v1/fulfillments", tags=["fulfillments"])


def get_fulfillment_service(request: Request) -> FulfillmentQueueService:
    return request.app.state.fulfillment_queue_service


@router.get(
    "",
    response_model=FulfillmentQueueResponse,
    responses=error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ),
)
async def list_fulfillments(
    _: ApiUser = Depends(require_role(UserRole.cashier)),
    service: FulfillmentQueueService = Depends(get_fulfillment_service),
) -> FulfillmentQueueResponse:
    items = await service.list_active()
    summary = await service.summary()
    return FulfillmentQueueResponse(
        items=[_item_response(item) for item in items],
        queuedQty=str(summary.queued_qty),
        usdtFact=str(summary.usdt_fact) if summary.usdt_fact is not None else None,
        queueShortageQty=(
            str(summary.queue_shortage_qty)
            if summary.queue_shortage_qty is not None
            else None
        ),
        onchainLiquidQty=(
            str(summary.onchain_liquid_qty)
            if summary.onchain_liquid_qty is not None
            else None
        ),
    )


@router.post(
    "/{item_id}/reorder",
    response_model=FulfillmentQueueItemResponse,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_409_CONFLICT,
    ),
)
async def reorder_fulfillment(
    item_id: int,
    payload: FulfillmentReorderRequest,
    user: ApiUser = Depends(require_role(UserRole.manager)),
    service: FulfillmentQueueService = Depends(get_fulfillment_service),
) -> FulfillmentQueueItemResponse:
    item = await service.reorder(
        ReorderFulfillment(
            item_id=item_id,
            before_item_id=payload.beforeItemId,
            actor_user_id=user.id,
        )
    )
    return _item_response(item)


@router.post(
    "/{item_id}/cancel",
    response_model=FulfillmentQueueItemResponse,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_409_CONFLICT,
    ),
)
async def cancel_fulfillment(
    item_id: int,
    payload: FulfillmentCancelRequest,
    _: ApiUser = Depends(require_role(UserRole.manager)),
    service: FulfillmentQueueService = Depends(get_fulfillment_service),
) -> FulfillmentQueueItemResponse:
    return _item_response(await service.cancel(item_id=item_id, reason=payload.reason))


def _item_response(item) -> FulfillmentQueueItemResponse:
    return FulfillmentQueueItemResponse(
        id=item.id,
        dealId=item.deal_id,
        requestKind=str(item.request_kind),
        qty=str(item.qty),
        sequenceNo=item.sequence_no,
        status=str(item.status),
        createdBy=item.created_by,
        createdAt=item.created_at,
        reorderedBy=item.reordered_by,
        reorderedAt=item.reordered_at,
    )
