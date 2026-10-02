from __future__ import annotations

import re
from urllib.parse import urlsplit

_CHAT_PATH = re.compile(
    r"^/(?:\+[A-Za-z0-9_-]+|joinchat/[A-Za-z0-9_-]+|[A-Za-z][A-Za-z0-9_]{4,31})/?$"
)


def normalize_telegram_invite_link(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() not in {"t.me", "telegram.me"}
        or parsed.query
        or parsed.fragment
        or not _CHAT_PATH.fullmatch(parsed.path)
    ):
        raise ValueError("Укажите ссылку вида https://t.me/+... или https://t.me/username")
    return f"https://t.me{parsed.path.rstrip('/')}"
