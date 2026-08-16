from __future__ import annotations

import hashlib
import hmac
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import (
    AuthError,
    create_access_token,
    decode_access_token,
    verify_telegram_login,
)
from api.exception_handlers import register_exception_handlers
from api.models import ApiUser, HealthResponse, LoginResponse, UserRole
from api.routers import auth as auth_router
from api.routers import health as health_router
from api.schemas.common import ErrorResponse

BOT_TOKEN = "123456:test-token"


def test_verify_telegram_login_accepts_valid_hash() -> None:
    payload = {
        "id": 42,
        "first_name": "Alex",
        "username": "alex",
        "auth_date": 1000,
    }
    payload["hash"] = _telegram_hash(payload)

    verify_telegram_login(payload, bot_token=BOT_TOKEN, now=1000)


def test_verify_telegram_login_rejects_invalid_hash() -> None:
    payload = {
        "id": 42,
        "first_name": "Alex",
        "auth_date": 1000,
        "hash": "bad",
    }

    with pytest.raises(AuthError):
        verify_telegram_login(payload, bot_token=BOT_TOKEN, now=1000)


def test_access_token_roundtrip() -> None:
    token = create_access_token(
        tg_user_id=42,
        bot_token=BOT_TOKEN,
        ttl_seconds=60,
        now=1000,
    )

    payload = decode_access_token(token, bot_token=BOT_TOKEN, now=1059)

    assert payload.sub == "42"
    assert payload.exp == 1060


def test_access_token_rejects_expired_token() -> None:
    token = create_access_token(
        tg_user_id=42,
        bot_token=BOT_TOKEN,
        ttl_seconds=60,
        now=1000,
    )

    with pytest.raises(AuthError):
        decode_access_token(token, bot_token=BOT_TOKEN, now=1060)


def test_access_token_rejects_tampered_signature() -> None:
    token = create_access_token(
        tg_user_id=42,
        bot_token=BOT_TOKEN,
        ttl_seconds=60,
        now=1000,
    )

    with pytest.raises(AuthError):
        decode_access_token(f"{token}x", bot_token=BOT_TOKEN, now=1000)


def test_me_allows_explicit_dev_auth_bypass() -> None:
    app = FastAPI()
    app.state.config = SimpleNamespace(
        api_dev_auth_bypass=True,
        api_dev_tg_user_id=42,
    )
    app.state.user_repository = _DevUserRepository()
    app.include_router(auth_router.router)

    response = TestClient(app).get("/api/v1/me")

    assert response.status_code == 200
    assert response.json()["role"] == "admin"


def test_health_route_returns_runtime_contract() -> None:
    app = FastAPI()
    app.include_router(health_router.router)

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    contract = HealthResponse.model_validate(response.json())
    assert contract.status == "ok"


def test_telegram_login_returns_token_user_and_cookie_contract() -> None:
    repository = _AuthUserRepository(active=True)
    client = TestClient(_auth_app(repository))
    payload = _login_payload()

    response = client.post("/api/v1/auth/telegram", json=payload)

    assert response.status_code == 200
    contract = LoginResponse.model_validate(response.json())
    assert contract.token_type == "bearer"
    assert contract.user.tg_user_id == 42
    assert client.cookies.get("crm_access_token") == contract.access_token
    assert repository.seeded_admin_ids == [42]


def test_telegram_login_returns_runtime_auth_error_contract() -> None:
    client = TestClient(_auth_app(_AuthUserRepository(active=True)))
    payload = _login_payload()
    payload["hash"] = "invalid"

    response = client.post("/api/v1/auth/telegram", json=payload)

    assert response.status_code == 401
    error = ErrorResponse.model_validate(response.json())
    assert error.detail == "Telegram login hash is invalid"


def test_telegram_login_returns_inactive_user_contract() -> None:
    client = TestClient(_auth_app(_AuthUserRepository(active=False)))

    response = client.post("/api/v1/auth/telegram", json=_login_payload())

    assert response.status_code == 403
    error = ErrorResponse.model_validate(response.json())
    assert error.detail == "CRM user is not active"


def test_me_returns_unauthorized_runtime_contract_without_credentials() -> None:
    response = TestClient(_auth_app(_AuthUserRepository(active=True))).get("/api/v1/me")

    assert response.status_code == 401
    error = ErrorResponse.model_validate(response.json())
    assert error.detail == "Not authenticated"


class _DevUserRepository:
    async def get_dev_user(self, tg_user_id: int | None) -> ApiUser:
        assert tg_user_id == 42
        return ApiUser(
            id=1,
            tg_user_id=42,
            display_name="Local Admin",
            role=UserRole.admin,
            cities=[],
            is_active=True,
        )


class _AuthUserRepository:
    def __init__(self, *, active: bool) -> None:
        self._active = active
        self.seeded_admin_ids: list[int] | None = None

    async def seed_from_managers(self, admin_ids: list[int]) -> None:
        self.seeded_admin_ids = admin_ids

    async def get_active_by_tg_user_id(self, tg_user_id: int) -> ApiUser | None:
        if not self._active:
            return None
        return ApiUser(
            id=1,
            tg_user_id=tg_user_id,
            display_name="Admin",
            role=UserRole.admin,
            cities=[],
            is_active=True,
        )


def _auth_app(repository: _AuthUserRepository) -> FastAPI:
    app = FastAPI()
    app.state.config = SimpleNamespace(
        bot_token=BOT_TOKEN,
        api_jwt_ttl_seconds=3600,
        admin_ids=[42],
        api_dev_auth_bypass=False,
        api_dev_tg_user_id=None,
    )
    app.state.user_repository = repository
    register_exception_handlers(app)
    app.include_router(auth_router.router)
    return app


def _login_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "id": 42,
        "first_name": "Admin",
        "auth_date": int(time.time()),
    }
    payload["hash"] = _telegram_hash(payload)
    return payload


def _telegram_hash(payload: dict[str, object]) -> str:
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(payload.items()) if key != "hash"
    )
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
