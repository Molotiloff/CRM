from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from aiogram.types import Chat, Message

from handlers.exchange_monitor import ExchangeMonitorHandler


@pytest.mark.parametrize("prefix", ["рап", "абс"])
async def test_exchange_commands_route_to_matching_book(prefix):
    monitor = ExchangeMonitorHandler(
        MagicMock(), orderbook_service=MagicMock(), command_prefix=prefix,
    )
    handlers = monitor.router.message.handlers
    for index, suffix in enumerate(["", "п", "-", "ред"]):
        message = Message(
            message_id=1, date=datetime.now(UTC),
            chat=Chat(id=1, type="private"), text=f"/{prefix}{suffix}",
        )
        matches = [await handler.check(message, bot=MagicMock()) for handler in handlers]
        assert [bool(result[0]) for result in matches] == [i == index for i in range(4)]
    legacy = Message(
        message_id=2, date=datetime.now(UTC),
        chat=Chat(id=1, type="private"), text="/гар",
    )
    for handler in handlers:
        assert not (await handler.check(legacy, bot=MagicMock()))[0]
