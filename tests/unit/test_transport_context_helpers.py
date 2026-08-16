from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import cast

from aiogram.types import Message

from services.number_formatting import format_rate
from telegram_adapters.message_context import actor_from_message, get_chat_name


def test_message_context_prefers_human_readable_names() -> None:
    message = cast(
        Message,
        SimpleNamespace(
            from_user=SimpleNamespace(full_name="Manager Name", username="manager", id=42),
            chat=SimpleNamespace(
                title=None,
                first_name="Client",
                last_name="Name",
                username="client",
            ),
        ),
    )

    assert actor_from_message(message) == "Manager Name"
    assert get_chat_name(message) == "Client Name"


def test_message_context_uses_group_title_and_unknown_actor_fallback() -> None:
    message = cast(
        Message,
        SimpleNamespace(
            from_user=None,
            chat=SimpleNamespace(
                title="Operations",
                first_name=None,
                last_name=None,
                username=None,
            ),
        ),
    )

    assert actor_from_message(message) == "unknown"
    assert get_chat_name(message) == "Operations"


def test_rate_formatting_is_shared_and_drops_insignificant_zeroes() -> None:
    assert format_rate(Decimal("81.50000000")) == "81.5"
    assert format_rate(Decimal("100")) == "100"
