from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from api.auth import create_access_token
from api.dependencies import get_config, get_current_user, get_user_repository
from api.models import (
    ApiUser,
    LoginResponse,
    TelegramOidcLoginRequest,
)
from api.openapi import error_responses
from api.repositories import UserRepository
from api.telegram_oidc import exchange_telegram_oidc_code
from config import Config

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.post(
    "/auth/telegram/oidc",
    response_model=LoginResponse,
    responses=error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def login_with_telegram_oidc(
    payload: TelegramOidcLoginRequest,
    response: Response,
    config: Config = Depends(get_config),
    user_repository: UserRepository = Depends(get_user_repository),
) -> LoginResponse:
    if not config.telegram_oidc_client_id or not config.telegram_oidc_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram OIDC is not configured",
        )

    tg_user_id = await exchange_telegram_oidc_code(
        code=payload.code,
        code_verifier=payload.code_verifier,
        redirect_uri=payload.redirect_uri,
        expected_nonce=payload.nonce,
        client_id=config.telegram_oidc_client_id,
        client_secret=config.telegram_oidc_client_secret,
    )
    return await _create_session(
        tg_user_id=tg_user_id,
        response=response,
        config=config,
        user_repository=user_repository,
    )


async def _create_session(
    *,
    tg_user_id: int,
    response: Response,
    config: Config,
    user_repository: UserRepository,
) -> LoginResponse:
    await user_repository.seed_from_managers(config.admin_ids)
    user = await user_repository.get_active_by_tg_user_id(tg_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CRM user is not active",
        )

    token = create_access_token(
        tg_user_id=user.tg_user_id,
        secret=config.crm_jwt_secret,
        ttl_seconds=config.api_jwt_ttl_seconds,
    )
    response.set_cookie(
        "crm_access_token",
        token,
        httponly=True,
        samesite="lax",
        secure=True,
        path="/",
        max_age=config.api_jwt_ttl_seconds,
    )
    return LoginResponse(access_token=token, user=user)


@router.get(
    "/me",
    response_model=ApiUser,
    responses=error_responses(status.HTTP_401_UNAUTHORIZED),
)
async def me(user: ApiUser = Depends(get_current_user)) -> ApiUser:
    return user
