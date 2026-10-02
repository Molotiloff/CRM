from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas.clients import ClientTelegramInviteLinkUpdate


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://t.me/+abc_DEF-123", "https://t.me/+abc_DEF-123"),
        ("https://t.me/skyex_chat", "https://t.me/skyex_chat"),
        (" https://telegram.me/joinchat/abc123/ ", "https://t.me/joinchat/abc123"),
        ("", None),
        (None, None),
    ],
)
def test_client_invite_link_is_normalized(value: str | None, expected: str | None) -> None:
    payload = ClientTelegramInviteLinkUpdate(telegramInviteLink=value)
    assert payload.telegramInviteLink == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://t.me/+abc123",
        "https://t.me.evil.example/+abc123",
        "https://t.me/+abc123?start=unexpected",
        "https://t.me/+abc123#fragment",
        "https://t.me/a",
        "javascript:alert(1)",
    ],
)
def test_client_invite_link_rejects_unsafe_or_non_invite_urls(value: str) -> None:
    with pytest.raises(ValidationError):
        ClientTelegramInviteLinkUpdate(telegramInviteLink=value)
