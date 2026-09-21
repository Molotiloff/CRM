from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import (
    AuthError,
    create_access_token,
    decode_access_token,
)
from api.exception_handlers import register_exception_handlers
from api.models import ApiUser, HealthResponse, LoginResponse, UserRole
from api.routers import auth as auth_router
from api.routers import health as health_router
from api.schemas.common import ErrorResponse

BOT_TOKEN = "123456:test-token"
JWT_SECRET = "test-jwt-secret-with-at-least-32-characters"


def test_access_token_roundtrip() -> None:
    token = create_access_token(
        tg_user_id=42,
        secret=JWT_SECRET,
        ttl_seconds=60,
        now=1000,
    )

    payload = decode_access_token(token, secret=JWT_SECRET, now=1059)

    assert payload.sub == "42"
    assert payload.exp == 1060


def test_access_token_rejects_expired_token() -> None:
    token = create_access_token(
        tg_user_id=42,
        secret=JWT_SECRET,
        ttl_seconds=60,
        now=1000,
    )

    with pytest.raises(AuthError):
        decode_access_token(token, secret=JWT_SECRET, now=1060)


def test_access_token_rejects_tampered_signature() -> None:
    token = create_access_token(
        tg_user_id=42,
        secret=JWT_SECRET,
        ttl_seconds=60,
        now=1000,
    )

    with pytest.raises(AuthError):
        decode_access_token(f"{token}x", secret=JWT_SECRET, now=1000)


def test_access_token_is_not_signed_with_bot_token() -> None:
    token = create_access_token(
        tg_user_id=42,
        secret=JWT_SECRET,
        ttl_seconds=60,
        now=1000,
    )

    with pytest.raises(AuthError):
        decode_access_token(token, secret=BOT_TOKEN, now=1000)


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


def test_legacy_telegram_login_route_is_removed() -> None:
    response = TestClient(_auth_app(_AuthUserRepository(active=True))).post(
        "/api/v1/auth/telegram",
        json={},
    )

    assert response.status_code == 404


def test_telegram_oidc_login_returns_token_user_and_cookie_contract(monkeypatch) -> None:
    async def exchange(**kwargs) -> int:
        assert kwargs["code"] == "telegram-code"
        assert kwargs["client_id"] == "8453451926"
        return 42

    monkeypatch.setattr(auth_router, "exchange_telegram_oidc_code", exchange)
    repository = _AuthUserRepository(active=True)
    client = TestClient(_auth_app(repository))

    response = client.post(
        "/api/v1/auth/telegram/oidc",
        json={
            "code": "telegram-code",
            "code_verifier": "v" * 43,
            "redirect_uri": "https://crm.example/api/auth/telegram/oidc/callback",
            "nonce": "n" * 32,
        },
    )

    assert response.status_code == 200
    contract = LoginResponse.model_validate(response.json())
    assert contract.user.tg_user_id == 42
    assert client.cookies.get("crm_access_token") == contract.access_token
    assert "Secure" in response.headers["set-cookie"]


def test_telegram_oidc_login_requires_configuration() -> None:
    app = _auth_app(_AuthUserRepository(active=True))
    app.state.config.telegram_oidc_client_id = None
    app.state.config.telegram_oidc_client_secret = None

    response = TestClient(app).post(
        "/api/v1/auth/telegram/oidc",
        json={
            "code": "telegram-code",
            "code_verifier": "v" * 43,
            "redirect_uri": "https://crm.example/api/auth/telegram/oidc/callback",
            "nonce": "n" * 32,
        },
    )

    assert response.status_code == 503


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
        crm_jwt_secret=JWT_SECRET,
        api_jwt_ttl_seconds=3600,
        admin_ids=[42],
        api_dev_auth_bypass=False,
        api_dev_tg_user_id=None,
        telegram_oidc_client_id="8453451926",
        telegram_oidc_client_secret="oidc-secret",
    )
    app.state.user_repository = repository
    register_exception_handlers(app)
    app.include_router(auth_router.router)
    return app
