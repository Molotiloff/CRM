from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, Message

from handlers.exchange_monitor import ExchangeMonitorHandler
from services.rate_order.widget_errors import WidgetDeliveryError


@pytest.mark.parametrize(
    "failure", [None, WidgetDeliveryError("bad_request"), RuntimeError("limit")]
)
async def test_private_live_uses_widget_or_text_fallback(failure):
    widget = SimpleNamespace(send=AsyncMock(side_effect=failure))
    book = SimpleNamespace(
        build_live_text=MagicMock(return_value="ASKS\nBIDS"), set_live_message=AsyncMock()
    )
    repo = SimpleNamespace(is_manager=AsyncMock(return_value=True))
    monitor = ExchangeMonitorHandler(
        repo, orderbook_service=book, command_prefix="рап", widget_service=widget
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=123, is_bot=False),
        answer=AsyncMock(),
    )
    await monitor._cmd_live(message)
    widget.send.assert_awaited_once_with(123)
    if failure is None:
        message.answer.assert_not_awaited()
        book.build_live_text.assert_not_called()
    else:
        message.answer.assert_awaited_once_with("ASKS\nBIDS")
    book.set_live_message.assert_not_awaited()


async def test_admin_group_keeps_live_text_without_attempting_widget():
    widget = SimpleNamespace(send=AsyncMock())
    book = SimpleNamespace(
        build_live_text=MagicMock(return_value="ASKS\nBIDS"), set_live_message=AsyncMock()
    )
    monitor = ExchangeMonitorHandler(
        SimpleNamespace(is_manager=AsyncMock(return_value=True)),
        orderbook_service=book,
        command_prefix="рап",
        admin_chat_ids=[-123],
        widget_service=widget,
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-123, type="supergroup"),
        from_user=SimpleNamespace(id=123, is_bot=False),
        answer=AsyncMock(
            return_value=SimpleNamespace(chat=SimpleNamespace(id=-123), message_id=42)
        ),
    )
    await monitor._cmd_live(message)
    widget.send.assert_not_awaited()
    message.answer.assert_awaited_once_with("ASKS\nBIDS")
    book.set_live_message.assert_awaited_once_with(chat_id=-123, message_id=42)


@pytest.mark.parametrize("prefix", ["рап", "абс"])
async def test_exchange_commands_route_to_matching_book(prefix):
    monitor = ExchangeMonitorHandler(
        MagicMock(),
        orderbook_service=MagicMock(),
        command_prefix=prefix,
    )
    handlers = monitor.router.message.handlers
    for index, suffix in enumerate(["", "п", "-", "ред"]):
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=1, type="private"),
            text=f"/{prefix}{suffix}",
        )
        matches = [await handler.check(message, bot=MagicMock()) for handler in handlers]
        assert [bool(result[0]) for result in matches] == [i == index for i in range(4)]
    legacy = Message(
        message_id=2,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        text="/гар",
    )
    for handler in handlers:
        assert not (await handler.check(legacy, bot=MagicMock()))[0]
