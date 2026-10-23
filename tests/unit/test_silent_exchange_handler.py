from unittest.mock import AsyncMock

import pytest

from handlers.accept_short import AcceptShortHandler
from middlewares.silent_accounting_chats import SilentAccountingChatsMiddleware
from tests.unit.test_silent_accounting_chats import _message


@pytest.mark.parametrize("text", ["/пр 2000 от 2000/83", "/пт 10 ор 830", "/пбат@skyex_bot 1000 ор 2700"])
async def test_silent_middleware_routes_exchange_before_wallet_parser(text):
    exchange = AsyncMock()
    wallet = AsyncMock()
    middleware = SilentAccountingChatsMiddleware({-200}, start_handler=AsyncMock(), wallet_change_handler=wallet, exchange_handler=exchange)
    await middleware(AsyncMock(), _message(text), {})
    exchange.assert_awaited_once()
    wallet.assert_not_awaited()


async def test_silent_handler_reuses_service_without_manager_check_or_reply():
    service = AsyncMock()
    repo = AsyncMock()
    handler = AcceptShortHandler(repo, service, silent_chat_ids={-200})
    message = _message("/пр 2000 от 2000/83")
    # An unbound Message would raise if the handler attempted a Telegram reply.
    await handler.on_silent_exchange(message)
    service.execute_silent_balance.assert_awaited_once()
    repo.is_manager.assert_not_awaited()
    service.execute_silent_balance.side_effect = ValueError("invalid command")
    await handler.on_silent_exchange(message)


async def test_silent_handler_does_not_accept_other_chats():
    service = AsyncMock()
    handler = AcceptShortHandler(AsyncMock(), service, silent_chat_ids={-200})
    await handler.on_silent_exchange(_message("/пр 2000 от 2000/83", chat_id=-300))
    service.execute_silent_balance.assert_not_awaited()
