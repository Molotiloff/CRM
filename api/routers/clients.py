from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status

from api.dependencies import get_current_user, require_role
from api.models import ApiUser, UserRole
from api.openapi import error_responses
from api.queries import ClientNotFoundError, ClientQueryService
from api.queries.clients import ClientListQuery
from api.schemas.clients import (
    ClientDto,
    ClientsPageResponse,
    ClientTelegramInviteLinkUpdate,
    ClientTransactionsResponse,
)

router = APIRouter(prefix="/api/v1", tags=["clients"])


def get_client_queries(request: Request) -> ClientQueryService:
    return request.app.state.client_queries


@router.get(
    "/clients",
    response_model=ClientsPageResponse,
    responses=error_responses(status.HTTP_401_UNAUTHORIZED),
)
async def list_clients(
    search: str | None = None,
    group: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: ApiUser = Depends(get_current_user),
    queries: ClientQueryService = Depends(get_client_queries),
) -> ClientsPageResponse:
    return await queries.list_clients(
        ClientListQuery(search=search, group=group, limit=limit, offset=offset)
    )


@router.get(
    "/clients/{client_id}",
    response_model=ClientDto,
    responses=error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_404_NOT_FOUND,
    ),
)
async def get_client(
    client_id: int,
    _: ApiUser = Depends(get_current_user),
    queries: ClientQueryService = Depends(get_client_queries),
) -> ClientDto:
    return await queries.get_client(client_id)


@router.patch(
    "/clients/{client_id}/telegram-invite-link",
    response_model=ClientDto,
    responses=error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    ),
)
async def update_client_telegram_invite_link(
    client_id: int,
    payload: ClientTelegramInviteLinkUpdate,
    request: Request,
    _: ApiUser = Depends(require_role(UserRole.manager)),
    queries: ClientQueryService = Depends(get_client_queries),
) -> ClientDto:
    async with request.app.state.container.pool.acquire() as connection:
        updated_id = await connection.fetchval(
            """UPDATE clients SET telegram_invite_link=$2
               WHERE id=$1 AND is_active RETURNING id""",
            client_id,
            payload.telegramInviteLink,
        )
    if updated_id is None:
        raise ClientNotFoundError(client_id)
    return await queries.get_client(client_id)


@router.get(
    "/clients/{client_id}/transactions",
    response_model=ClientTransactionsResponse,
    responses=error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_404_NOT_FOUND,
    ),
)
async def get_client_transactions(
    client_id: int,
    limit: int = Query(50, ge=1, le=200),
    _: ApiUser = Depends(get_current_user),
    queries: ClientQueryService = Depends(get_client_queries),
) -> ClientTransactionsResponse:
    return await queries.get_transactions(client_id, limit=limit)
