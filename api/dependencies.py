from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import Config

from .auth import AuthError, decode_access_token
from .models import ApiUser, UserRole
from .repositories import UserRepository

bearer_scheme = HTTPBearer(auto_error=False)

ROLE_LEVELS: dict[UserRole, int] = {
    UserRole.cashier: 10,
    UserRole.manager: 20,
    UserRole.accountant: 30,
    UserRole.owner: 40,
    UserRole.admin: 50,
}


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_user_repository(request: Request) -> UserRepository:
    return request.app.state.user_repository


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    config: Config = Depends(get_config),
    user_repository: UserRepository = Depends(get_user_repository),
) -> ApiUser:
    token = request.cookies.get("crm_access_token")
    if credentials is not None:
        token = credentials.credentials
    if not token:
        if config.api_dev_auth_bypass:
            dev_user = await user_repository.get_dev_user(config.api_dev_tg_user_id)
            if dev_user is not None:
                return dev_user
        raise _unauthorized()

    try:
        payload = decode_access_token(token, secret=config.crm_jwt_secret)
        tg_user_id = int(payload.sub)
    except (AuthError, ValueError):
        raise _unauthorized() from None

    user = await user_repository.get_active_by_tg_user_id(tg_user_id)
    if user is None:
        raise _unauthorized()
    return user


def require_role(min_role: UserRole) -> Callable[[ApiUser], ApiUser]:
    def dependency(user: ApiUser = Depends(get_current_user)) -> ApiUser:
        if ROLE_LEVELS[user.role] < ROLE_LEVELS[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role",
            )
        return user

    return dependency


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
