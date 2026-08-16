from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from api.auth import create_access_token, verify_telegram_login
from api.dependencies import get_config, get_current_user, get_user_repository
from api.models import ApiUser, LoginResponse, TelegramLoginRequest
from api.openapi import error_responses
from api.repositories import UserRepository
from config import Config

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.post(
    "/auth/telegram",
    response_model=LoginResponse,
    responses=error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ),
)
async def login_with_telegram(
    payload: TelegramLoginRequest,
    response: Response,
    config: Config = Depends(get_config),
    user_repository: UserRepository = Depends(get_user_repository),
) -> LoginResponse:
    data = payload.model_dump()
    verify_telegram_login(data, bot_token=config.bot_token)

    await user_repository.seed_from_managers(config.admin_ids)
    user = await user_repository.get_active_by_tg_user_id(payload.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CRM user is not active",
        )

    token = create_access_token(
        tg_user_id=user.tg_user_id,
        bot_token=config.bot_token,
        ttl_seconds=config.api_jwt_ttl_seconds,
    )
    response.set_cookie(
        "crm_access_token",
        token,
        httponly=True,
        samesite="lax",
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
