from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status

from api.dependencies import require_role
from api.models import ApiUser, UserRole
from api.openapi import error_responses
from api.presentation.deals import build_deal_details, build_deals_page
from api.schemas.deals import (
    ClientTransferCreateRequest,
    DealCancelRequest,
    DealCreateRequest,
    DealDetailsResponse,
    DealFormContextDto,
    DealSourceEditRequest,
    DealsPageResponse,
    DealStatus,
    DealStatusRequest,
    DealType,
    DealUpdateRequest,
    SettlementResolutionResponse,
    SettlementReviewResolutionRequest,
)
from domain import DomainValidationError
from domain.accounting_flows import SettlementResolution as DomainSettlementResolution
from services.crm.client_transfer_service import ClientTransferCommand, ClientTransferService
from services.crm.deal_service import (
    DealCreateCommand,
    DealListFilter,
    DealService,
    DealStatusCommand,
    DealUpdateCommand,
)
from services.crm.deal_source_mutation import (
    CashSourceEdit,
    DealSourceMutationService,
    ExchangeSourceEdit,
)
from services.payment_watch.settlement_models import (
    ReplacementExchange,
    ResolveSettlementCommand,
)
from services.payment_watch.settlement_service import DealSettlementService

router = APIRouter(prefix="/api/v1", tags=["deals"])


def get_deal_service(request: Request) -> DealService:
    return request.app.state.deal_service


def get_client_transfer_service(request: Request) -> ClientTransferService:
    return request.app.state.client_transfer_service


@router.get(
    "/deals/schema",
    response_model=DealFormContextDto,
    responses=error_responses(status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
)
async def get_deal_form_context(
    request: Request,
    _: ApiUser = Depends(require_role(UserRole.manager)),
) -> DealFormContextDto:
    repository = request.app.state.container.crm_repositories.client_reads
    clients: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await repository.list_clients(limit=500, offset=offset)
        clients.extend(page)
        if len(page) < 500:
            break
        offset += 500
    return DealFormContextDto.model_validate({
        "cities": ["Екб", "Члб", "Тюмень", "Мск"],
        "counterparties": [],
        "clients": [{"id": str(row["id"]), "name": row["name"]} for row in clients],
        "companyRates": {},
        "defaultCounterpartyPercent": 0,
    })


def get_deal_source_mutation_service(request: Request) -> DealSourceMutationService:
    return request.app.state.deal_source_mutation_service


def get_settlement_service(request: Request) -> DealSettlementService:
    return request.app.state.deal_settlement_service


@router.get(
    "/deals",
    response_model=DealsPageResponse,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ),
)
async def list_deals(
    statuses: list[DealStatus] | None = Query(default=None, alias="status"),
    city: str | None = None,
    deal_type: DealType | None = Query(default=None, alias="type"),
    client_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: ApiUser = Depends(require_role(UserRole.cashier)),
    service: DealService = Depends(get_deal_service),
) -> DealsPageResponse:
    rows = await service.list_deals(
        DealListFilter(
            statuses=tuple(item.value for item in statuses or []),
            city=city,
            deal_type=deal_type.value if deal_type else None,
            client_id=client_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
    )
    return build_deals_page(rows)


@router.post(
    "/deals",
    response_model=DealDetailsResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_409_CONFLICT,
    ),
)
async def create_deal(
    payload: DealCreateRequest,
    user: ApiUser = Depends(require_role(UserRole.manager)),
    service: DealService = Depends(get_deal_service),
) -> DealDetailsResponse:
    if payload.dealType is DealType.client_transfer:
        raise DomainValidationError("Используйте маршрут перевода между клиентами")
    row = await service.create_deal(
        DealCreateCommand(
            deal_type=payload.dealType.value,
            city=payload.city,
            actor_user_id=user.id,
            client_id=payload.clientId,
            counterparty_id=payload.counterpartyId,
            comment=payload.comment,
            tronscan_url=payload.tronscanUrl,
            body=payload.body,
            profit=payload.profitRub,
            deal_at=payload.dealAt,
            exchange_client_req_id=payload.exchangeClientReqId,
        )
    )
    return build_deal_details(row)


@router.post(
    "/deals/client-transfers",
    response_model=DealDetailsResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_409_CONFLICT,
    ),
)
async def create_client_transfer(
    payload: ClientTransferCreateRequest,
    user: ApiUser = Depends(require_role(UserRole.manager)),
    transfer_service: ClientTransferService = Depends(get_client_transfer_service),
    deal_service: DealService = Depends(get_deal_service),
) -> DealDetailsResponse:
    result = await transfer_service.transfer(
        ClientTransferCommand(
            from_client_id=payload.fromClientId,
            to_client_id=payload.toClientId,
            amount=payload.amount,
            currency=payload.currency,
            source="crm",
            source_ref=payload.idempotencyKey,
            city="внутренний",
            actor_user_id=user.id,
            comment=payload.comment,
            allow_negative=payload.allowNegative,
        )
    )
    return build_deal_details(await deal_service.get_deal(result.deal_id))


@router.get(
    "/deals/{deal_id}",
    response_model=DealDetailsResponse,
    responses=error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    ),
)
async def get_deal(
    deal_id: int,
    _: ApiUser = Depends(require_role(UserRole.cashier)),
    service: DealService = Depends(get_deal_service),
) -> DealDetailsResponse:
    return build_deal_details(await service.get_deal(deal_id))


@router.patch(
    "/deals/{deal_id}",
    response_model=DealDetailsResponse,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
    ),
)
async def update_deal(
    deal_id: int,
    payload: DealUpdateRequest,
    _: ApiUser = Depends(require_role(UserRole.manager)),
    service: DealService = Depends(get_deal_service),
) -> DealDetailsResponse:
    field_map = {
        "city": "city",
        "clientId": "client_id",
        "counterpartyId": "counterparty_id",
        "comment": "comment",
        "tronscanUrl": "tronscan_url",
        "body": "body",
        "profitRub": "profit",
        "dealAt": "deal_at",
    }
    changes: dict[str, Any] = {
        field_map[field]: getattr(payload, field)
        for field in payload.model_fields_set
    }
    command = DealUpdateCommand(**changes)
    return build_deal_details(await service.update_deal(deal_id, command))


@router.post(
    "/deals/{deal_id}/status",
    response_model=DealDetailsResponse,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
    ),
)
async def change_deal_status(
    deal_id: int,
    payload: DealStatusRequest,
    user: ApiUser = Depends(require_role(UserRole.manager)),
    service: DealService = Depends(get_deal_service),
) -> DealDetailsResponse:
    event_payload = dict(payload.payload)
    if payload.comment is not None:
        event_payload["comment"] = payload.comment
    row = await service.change_status(
        deal_id,
        DealStatusCommand(
            status=payload.status.value,
            actor_user_id=user.id,
            payload=event_payload,
        ),
    )
    return build_deal_details(row)


@router.post(
    "/settlements/{settlement_id}/resolve",
    response_model=SettlementResolutionResponse,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_409_CONFLICT,
    ),
)
async def resolve_settlement_review(
    settlement_id: int,
    payload: SettlementReviewResolutionRequest,
    user: ApiUser = Depends(require_role(UserRole.manager)),
    service: DealSettlementService = Depends(get_settlement_service),
) -> SettlementResolutionResponse:
    replacement = (
        ReplacementExchange(
            request_id=payload.replacement.requestId,
            table_request_id=payload.replacement.tableRequestId,
            recv_code=payload.replacement.recvCode.upper(),
            recv_amount=payload.replacement.recvAmount,
            pay_code=payload.replacement.payCode.upper(),
            pay_amount=payload.replacement.payAmount,
            rate=payload.replacement.rate,
        )
        if payload.replacement is not None
        else None
    )
    result = await service.resolve_review(
        ResolveSettlementCommand(
            settlement_id=settlement_id,
            resolution=DomainSettlementResolution(payload.resolution.value),
            actor_user_id=user.id,
            comment=payload.comment,
            replacement=replacement,
        )
    )
    return SettlementResolutionResponse(
        settlementId=result.settlement_id,
        dealId=result.deal_id,
        status=str(result.status),
        resolution=payload.resolution,
        expected=result.expected,
        actual=result.actual,
        delta=result.delta,
    )


@router.patch(
    "/deals/{deal_id}/source",
    response_model=DealDetailsResponse,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
    ),
)
async def edit_deal_source(
    deal_id: int,
    payload: DealSourceEditRequest,
    user: ApiUser = Depends(require_role(UserRole.manager)),
    service: DealSourceMutationService = Depends(get_deal_source_mutation_service),
) -> DealDetailsResponse:
    if payload.exchange is not None:
        item = payload.exchange
        row = await service.edit_exchange(
            deal_id,
            ExchangeSourceEdit(
                operation_id=item.operationId,
                recv_code=item.recvCode,
                recv_amount=item.recvAmount,
                pay_code=item.payCode,
                pay_amount=item.payAmount,
                rate=item.rate,
                note=item.note,
            ),
            actor_name=user.display_name,
        )
    else:
        item = payload.cash
        assert item is not None
        row = await service.edit_cash(
            deal_id,
            CashSourceEdit(
                city=item.city,
                amount=item.amount,
                in_amount=item.inAmount,
                out_amount=item.outAmount,
                comment=item.comment,
                contact1=item.contact1,
                contact2=item.contact2,
            ),
            actor_name=user.display_name,
        )
    return build_deal_details(row)


@router.post(
    "/deals/{deal_id}/cancel",
    response_model=DealDetailsResponse,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
    ),
)
async def cancel_deal_source(
    deal_id: int,
    payload: DealCancelRequest,
    user: ApiUser = Depends(require_role(UserRole.manager)),
    service: DealSourceMutationService = Depends(get_deal_source_mutation_service),
) -> DealDetailsResponse:
    row = await service.cancel(
        deal_id,
        actor_user_id=user.id,
        comment=payload.comment,
    )
    return build_deal_details(row)
