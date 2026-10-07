from __future__ import annotations

import asyncio
import hashlib
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status

from api.dependencies import require_role
from api.models import ApiUser, UserRole
from api.openapi import error_responses
from api.presentation.deals import build_deal_details, build_deals_page
from api.schemas.deals import (
    CashDealCreateRequest,
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
    ExchangeDealCreateRequest,
    ReferrerSpreadPayRequest,
    SettlementResolutionResponse,
    SettlementReviewResolutionRequest,
)
from domain import DomainStateError, DomainValidationError
from domain.accounting_flows import SettlementResolution as DomainSettlementResolution
from services.cash_requests.create_cash_request import CreateCashRequest, CreateCashRequestParams
from services.cash_requests.parsing import ParsedRequest
from services.crm.client_transfer_service import ClientTransferCommand
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
from services.crm.telegram_deal_registrar import exchange_type_from_firm_perspective
from services.exchange.create_exchange_request import CreateExchangeParams
from services.messaging import ClientChatReplier, DeferredMessenger
from services.payment_watch.settlement_models import (
    ReplacementExchange,
    ResolveSettlementCommand,
)
from services.payment_watch.settlement_service import DealSettlementService
from services.request_table.table_done_service import RequestTableDoneService

router = APIRouter(prefix="/api/v1", tags=["deals"])
_exchange_creation_lock = asyncio.Lock()
_cash_creation_lock = asyncio.Lock()


def get_deal_service(request: Request) -> DealService:
    return request.app.state.deal_service


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
        "clients": [
            {"id": str(row["id"]), "name": row["name"], "chatId": str(row["chat_id"])}
            for row in clients
        ],
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
    request: Request,
    user: ApiUser = Depends(require_role(UserRole.manager)),
    deal_service: DealService = Depends(get_deal_service),
) -> DealDetailsResponse:
    container = request.app.state.container
    if isinstance(container.messenger, DeferredMessenger):
        raise DomainStateError("Отправка чеков в Telegram сейчас недоступна")
    result = await container.crm.client_transfer_workflow.transfer(
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


@router.post(
    "/deals/exchanges",
    response_model=DealDetailsResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN, status.HTTP_409_CONFLICT,
    ),
)
async def create_exchange_deal(
    payload: ExchangeDealCreateRequest,
    request: Request,
    user: ApiUser = Depends(require_role(UserRole.manager)),
    service: DealService = Depends(get_deal_service),
) -> DealDetailsResponse:
    received, paid = payload.recvCode.strip().upper(), payload.payCode.strip().upper()
    allowed = {"RUB", "USDT", "USD", "USDW", "EUR", "EUR500", "THB"}
    if received not in allowed or paid not in allowed or received == paid:
        raise DomainValidationError("Неверная пара валют")
    if exchange_type_from_firm_perspective(received, paid) != payload.dealType.value:
        raise DomainValidationError("Направление валют не соответствует типу сделки")
    container = request.app.state.container
    if not container.config.request_chat_id or isinstance(container.messenger, DeferredMessenger):
        raise DomainStateError("Отправка в заявочный чат сейчас недоступна")
    async with container.pool.acquire() as con:
        client = await con.fetchrow(
            "SELECT chat_id, name FROM clients WHERE id=$1 AND is_active=TRUE",
            payload.clientId,
        )
    if client is None:
        raise DomainValidationError("Активный клиент не найден")
    if (payload.referrerPercent > 0 or payload.referrerSpreadRub > 0) and payload.referrerClientId is None:
        raise DomainValidationError("Для вознаграждения КТ выберите чат клиента-КТ")
    if payload.referrerClientId is not None:
        if payload.referrerClientId == payload.clientId:
            raise DomainValidationError("Клиент и КТ должны быть разными чатами")
        async with container.pool.acquire() as con:
            referrer_exists = await con.fetchval(
                "SELECT EXISTS(SELECT 1 FROM clients WHERE id=$1 AND is_active=TRUE)",
                payload.referrerClientId,
            )
        if not referrer_exists:
            raise DomainValidationError("Активный чат КТ не найден")
    chat_id = int(client["chat_id"])
    source_message_id = int.from_bytes(
        hashlib.blake2b(payload.idempotencyKey.encode(), digest_size=8).digest(), "big"
    ) & ((1 << 63) - 1)
    source_ref = f"{chat_id}:{source_message_id}"
    async with _exchange_creation_lock:
        existing = await service.get_source_exchange(source_ref)
        if existing is not None:
            response = build_deal_details(await service.get_deal(existing.id))
            link = await container.operational_repositories.exchange_requests.get_exchange_request_link(
                client_req_id=existing.exchange_client_req_id
            ) if existing.exchange_client_req_id else None
            response.requestChatPosted = bool(link and link.get("request_message_id"))
            return response
        result = await container.exchange.accept_short.create_request(
            CreateExchangeParams(
                chat_id=chat_id,
                chat_name=str(client["name"]),
                source_message_id=source_message_id,
                recv_code=received,
                recv_amount_expr=str(payload.recvAmount),
                pay_code=paid,
                pay_amount_expr=str(payload.payAmount),
                creator_name=user.display_name,
                note=payload.comment,
                source="crm",
                actor_user_id=user.id,
                city=payload.city,
                referrer_client_id=payload.referrerClientId,
                referrer_percent=payload.referrerPercent,
                referrer_spread_rub=payload.referrerSpreadRub,
            ),
            messenger=container.messenger,
            replier=ClientChatReplier(container.messenger, chat_id),
        )
        if not result.ok:
            raise DomainStateError(result.error or "Не удалось создать заявку")
        deal = await service.get_source_exchange(source_ref)
        if deal is None:
            raise DomainStateError("Заявка создана, но сделка CRM не найдена")
        response = build_deal_details(await service.get_deal(deal.id))
        response.requestChatPosted = result.request_chat_posted
        return response


@router.post(
    "/deals/cash",
    response_model=DealDetailsResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN, status.HTTP_409_CONFLICT,
    ),
)
async def create_cash_deal(
    payload: CashDealCreateRequest,
    request: Request,
    user: ApiUser = Depends(require_role(UserRole.manager)),
    service: DealService = Depends(get_deal_service),
) -> DealDetailsResponse:
    currency = payload.currency.strip().upper()
    if currency not in {"EUR", "EUR500", "USD", "RUB", "USDW", "THB"}:
        raise DomainValidationError("Недоступная валюта кассовой заявки")
    container = request.app.state.container
    if isinstance(container.messenger, DeferredMessenger):
        raise DomainStateError("Отправка заявок в Telegram сейчас недоступна")
    city = container.cash.router.normalize_city(payload.city)
    request_chat_id = container.cash.router.pick_request_chat_for_city(city)
    if request_chat_id is None or container.cash.router.city_by_request_chat(request_chat_id) != city:
        raise DomainValidationError("Для города не настроен чат предстоящих сделок")
    if payload.time and not container.cash.router.pick_schedule_chat_for_city(city):
        raise DomainValidationError("Для города не настроен чат времени")
    async with container.pool.acquire() as con:
        client = await con.fetchrow(
            "SELECT chat_id, name FROM clients WHERE id=$1 AND is_active=TRUE",
            payload.clientId,
        )
    if client is None:
        raise DomainValidationError("Активный клиент не найден")
    chat_id = int(client["chat_id"])
    source_message_id = int.from_bytes(
        hashlib.blake2b(payload.idempotencyKey.encode(), digest_size=8).digest(), "big"
    ) & ((1 << 63) - 1)
    source_ref = f"{chat_id}:{source_message_id}"
    async with _cash_creation_lock:
        async with container.pool.acquire() as con:
            existing_id = await con.fetchval(
                """SELECT id FROM deals WHERE source='crm' AND source_kind='cash'
                   AND source_ref=$1""",
                source_ref,
            )
        if existing_id is not None:
            existing = await service.get_deal(int(existing_id))
            response = build_deal_details(existing)
            entry = await container.operational_repositories.cash_requests.get_request_schedule_entry_by_req_id(
                req_id=str(existing.body.get("req_id"))
            )
            response.requestChatPosted = bool(entry and entry.get("request_message_id"))
            return response

        async def sync_schedule(schedule_city: str) -> None:
            await container.cash.schedule.sync_board(
                messenger=container.messenger, city=schedule_city
            )

        use_case = CreateCashRequest(
            repo=container.operational_repositories.cash_requests,
            router_service=container.cash.router,
            schedule_service=container.cash.schedule,
            deal_registrar=container.crm.telegram_registrar,
            calculator=container.cash.calculator,
            card_presenter=container.cash.card_presenter,
            card_parser=container.cash.card_parser,
            schedule_coordinator=container.cash.schedule_coordinator,
            metrics=container.metrics,
        )
        result = await use_case.execute_core(
            CreateCashRequestParams(
                chat_id=chat_id,
                chat_name=str(client["name"]),
                source_message_id=source_message_id,
                parsed=ParsedRequest(
                    cmd="crm",
                    kind="dep" if payload.dealType is DealType.deposit else "wd",
                    city=city,
                    amount_expr=str(payload.amount),
                    code=currency,
                    contact1=payload.contact1 or "",
                    contact2=payload.contact2 or "",
                    comment=payload.comment or "",
                ),
                creator_name=user.display_name,
                source="crm",
                actor_user_id=user.id,
                hhmm=payload.time,
            ),
            messenger=container.messenger,
            replier=ClientChatReplier(container.messenger, chat_id),
            sync_schedule_board=sync_schedule,
        )
        if not result.ok:
            raise DomainStateError(result.error or "Не удалось создать кассовую заявку")
        async with container.pool.acquire() as con:
            deal_id = await con.fetchval(
                """SELECT id FROM deals WHERE source='crm' AND source_kind='cash'
                   AND source_ref=$1""",
                source_ref,
            )
        if deal_id is None:
            raise DomainStateError("Заявка отправлена, но сделка CRM не найдена")
        response = build_deal_details(await service.get_deal(int(deal_id)))
        response.requestChatPosted = result.request_chat_posted
        return response


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


@router.post(
    "/deals/{deal_id}/table",
    response_model=DealDetailsResponse,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
    ),
)
async def write_exchange_deal_to_table(
    deal_id: int,
    request: Request,
    user: ApiUser = Depends(require_role(UserRole.manager)),
    service: DealService = Depends(get_deal_service),
) -> DealDetailsResponse:
    deal = await service.get_deal(deal_id)
    if str(deal.deal_type) not in {"sale", "purchase", "conversion"} or str(deal.source_kind) != "exchange":
        raise DomainValidationError("Кнопка доступна только для покупки, продажи и конвертации")
    if str(deal.status) not in {"new", "done"}:
        raise DomainValidationError("Заявка уже перешла в другой статус")
    if not deal.exchange_client_req_id:
        raise DomainValidationError("У сделки нет связанной заявки")
    container = request.app.state.container
    repo = container.operational_repositories.exchange_requests
    link = await repo.get_exchange_request_link(client_req_id=deal.exchange_client_req_id)
    if link is None or not link.get("table_req_id"):
        raise DomainValidationError("Не найдены параметры записи в таблицу")
    table_req_id = str(link["table_req_id"])
    await RequestTableDoneService(
        sheets_gateway=container.sheets_gateway
    ).write_exchange_request_once(
        table_req_id=table_req_id,
        repo=repo,
        deal_service=service,
        message_dt=deal.created_at,
        actor_user_id=user.id,
    )
    return build_deal_details(await service.get_deal(deal_id))


@router.post(
    "/deals/{deal_id}/referrer-spread",
    response_model=DealDetailsResponse,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT,
    ),
)
async def pay_referrer_spread(
    deal_id: int,
    payload: ReferrerSpreadPayRequest,
    user: ApiUser = Depends(require_role(UserRole.manager)),
    service: DealService = Depends(get_deal_service),
) -> DealDetailsResponse:
    return build_deal_details(await service.pay_referrer_spread(
        deal_id, actor_user_id=user.id, actor_tg_user_id=user.tg_user_id,
        expected_amount=payload.expectedAmount, expected_referrer_id=payload.referrerClientId,
    ))


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
        cash_item = payload.cash
        assert cash_item is not None
        row = await service.edit_cash(
            deal_id,
            CashSourceEdit(
                city=cash_item.city,
                amount=cash_item.amount,
                in_amount=cash_item.inAmount,
                out_amount=cash_item.outAmount,
                comment=cash_item.comment,
                contact1=cash_item.contact1,
                contact2=cash_item.contact2,
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
