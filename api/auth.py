from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class AuthError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class JwtPayload:
    sub: str
    exp: int


def verify_telegram_login(
    payload: Mapping[str, Any],
    *,
    bot_token: str,
    max_age_seconds: int = 24 * 60 * 60,
    now: int | None = None,
) -> None:
    expected_hash = str(payload.get("hash", ""))
    if not expected_hash:
        raise AuthError("Telegram login hash is missing")

    auth_date = int(payload.get("auth_date", 0) or 0)
    current_time = int(time.time() if now is None else now)
    if auth_date <= 0 or current_time - auth_date > max_age_seconds:
        raise AuthError("Telegram login payload is expired")

    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(payload.items())
        if key != "hash" and value is not None
    )
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    actual_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(actual_hash, expected_hash):
        raise AuthError("Telegram login hash is invalid")


def create_access_token(
    *,
    tg_user_id: int,
    bot_token: str,
    ttl_seconds: int,
    now: int | None = None,
) -> str:
    current_time = int(time.time() if now is None else now)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": str(tg_user_id), "exp": current_time + ttl_seconds}
    signing_input = ".".join((_b64_json(header), _b64_json(payload)))
    signature = _sign(signing_input, bot_token)
    return f"{signing_input}.{signature}"


def decode_access_token(token: str, *, bot_token: str, now: int | None = None) -> JwtPayload:
    try:
        header_part, payload_part, signature_part = token.split(".", 2)
    except ValueError:
        raise AuthError("Malformed token") from None

    signing_input = f"{header_part}.{payload_part}"
    expected_signature = _sign(signing_input, bot_token)
    if not hmac.compare_digest(expected_signature, signature_part):
        raise AuthError("Invalid token signature")

    header = _decode_json(header_part)
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise AuthError("Unsupported token header")

    payload = _decode_json(payload_part)
    sub = str(payload.get("sub", ""))
    exp = int(payload.get("exp", 0) or 0)
    current_time = int(time.time() if now is None else now)
    if not sub or exp <= current_time:
        raise AuthError("Token is expired")
    return JwtPayload(sub=sub, exp=exp)


def _sign(signing_input: str, bot_token: str) -> str:
    digest = hmac.new(
        bot_token.encode(),
        signing_input.encode(),
        hashlib.sha256,
    ).digest()
    return _b64(digest)


def _b64_json(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return _b64(raw)


def _decode_json(value: str) -> dict[str, Any]:
    try:
        return json.loads(_unb64(value))
    except (ValueError, TypeError, json.JSONDecodeError):
        raise AuthError("Malformed token payload") from None


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")
