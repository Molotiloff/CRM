from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Any

import httpx
import jwt

from api.auth import AuthError

TELEGRAM_ISSUER = "https://oauth.telegram.org"
TELEGRAM_TOKEN_URL = f"{TELEGRAM_ISSUER}/token"
TELEGRAM_JWKS_URL = f"{TELEGRAM_ISSUER}/.well-known/jwks.json"
ALLOWED_ALGORITHMS = frozenset({"RS256"})


async def exchange_telegram_oidc_code(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    expected_nonce: str,
    client_id: str,
    client_secret: str,
    http_client: httpx.AsyncClient | None = None,
) -> int:
    if http_client is not None:
        return await _exchange(
            http_client,
            code=code,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            expected_nonce=expected_nonce,
            client_id=client_id,
            client_secret=client_secret,
        )

    async with httpx.AsyncClient(timeout=10.0) as client:
        return await _exchange(
            client,
            code=code,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            expected_nonce=expected_nonce,
            client_id=client_id,
            client_secret=client_secret,
        )


async def _exchange(
    client: httpx.AsyncClient,
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    expected_nonce: str,
    client_id: str,
    client_secret: str,
) -> int:
    try:
        token_response = await client.post(
            TELEGRAM_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            },
            auth=httpx.BasicAuth(client_id, client_secret),
        )
        token_response.raise_for_status()
        id_token = str(token_response.json().get("id_token", ""))
        if not id_token:
            raise AuthError("Telegram OIDC response has no ID token")

        jwks_response = await client.get(TELEGRAM_JWKS_URL)
        jwks_response.raise_for_status()
        claims = _decode_id_token(
            id_token,
            jwks=jwks_response.json(),
            client_id=client_id,
        )
    except AuthError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, jwt.PyJWTError):
        raise AuthError("Telegram OIDC authentication failed") from None

    nonce = str(claims.get("nonce", ""))
    if not nonce or not hmac.compare_digest(nonce, expected_nonce):
        raise AuthError("Telegram OIDC nonce is invalid")

    try:
        tg_user_id = int(claims["id"])
    except (KeyError, TypeError, ValueError):
        raise AuthError("Telegram OIDC user ID is invalid") from None
    if tg_user_id <= 0:
        raise AuthError("Telegram OIDC user ID is invalid")
    return tg_user_id


def _decode_id_token(
    id_token: str,
    *,
    jwks: Mapping[str, Any],
    client_id: str,
) -> dict[str, Any]:
    header = jwt.get_unverified_header(id_token)
    algorithm = str(header.get("alg", ""))
    key_id = str(header.get("kid", ""))
    if algorithm not in ALLOWED_ALGORITHMS or not key_id:
        raise AuthError("Telegram OIDC signing key is invalid")

    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise AuthError("Telegram OIDC key set is invalid")
    key_data = next(
        (
            item
            for item in keys
            if isinstance(item, dict)
            and str(item.get("kid", "")) == key_id
            and str(item.get("alg", algorithm)) == algorithm
        ),
        None,
    )
    if key_data is None:
        raise AuthError("Telegram OIDC signing key was not found")

    signing_key = jwt.PyJWK.from_dict(key_data, algorithm=algorithm)
    return jwt.decode(
        id_token,
        signing_key.key,
        algorithms=[algorithm],
        audience=client_id,
        issuer=TELEGRAM_ISSUER,
        options={
            "require": ["aud", "exp", "iat", "iss", "sub", "id", "nonce"],
        },
    )
