from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from api.auth import AuthError
from api.telegram_oidc import (
    TELEGRAM_ISSUER,
    TELEGRAM_JWKS_URL,
    TELEGRAM_TOKEN_URL,
    exchange_telegram_oidc_code,
)

CLIENT_ID = "8453451926"
NONCE = "expected-nonce-value"


@pytest.mark.asyncio
async def test_oidc_exchange_validates_id_token_and_returns_telegram_user_id() -> None:
    id_token, jwks = _signed_id_token(nonce=NONCE)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TELEGRAM_TOKEN_URL:
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"id_token": id_token}, request=request)
        if str(request.url) == TELEGRAM_JWKS_URL:
            return httpx.Response(200, json=jwks, request=request)
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tg_user_id = await exchange_telegram_oidc_code(
            code="telegram-code",
            code_verifier="v" * 43,
            redirect_uri="https://crm.example/api/auth/telegram/oidc/callback",
            expected_nonce=NONCE,
            client_id=CLIENT_ID,
            client_secret="secret",
            http_client=client,
        )

    assert tg_user_id == 42


@pytest.mark.asyncio
async def test_oidc_exchange_rejects_wrong_nonce() -> None:
    id_token, jwks = _signed_id_token(nonce="other-nonce")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {"id_token": id_token} if str(request.url) == TELEGRAM_TOKEN_URL else jwks
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AuthError, match="nonce"):
            await exchange_telegram_oidc_code(
                code="telegram-code",
                code_verifier="v" * 43,
                redirect_uri="https://crm.example/api/auth/telegram/oidc/callback",
                expected_nonce=NONCE,
                client_id=CLIENT_ID,
                client_secret="secret",
                http_client=client,
            )


def _signed_id_token(*, nonce: str) -> tuple[str, dict[str, object]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "telegram-test-key", "alg": "RS256", "use": "sig"})
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": TELEGRAM_ISSUER,
            "aud": CLIENT_ID,
            "sub": "telegram-user",
            "id": 42,
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "telegram-test-key"},
    )
    return token, {"keys": [public_jwk]}
