from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import User

from handlers.wallets import WalletsHandler
from middlewares.silent_accounting_chats import SilentAccountingChatsMiddleware
from services.cash_requests.constants import CMD_MAP
from services.wallets.command_parser import WalletCommandParser
from telegram_adapters import ChatLockRegistry, CityCashMediaStore
from tests.unit.test_silent_accounting_chats import _message


@pytest.mark.parametrize("command,direction,currency", [
    (command, kind, currency) for command, (kind, currency) in CMD_MAP.items()
] + [("депп", "dep", "EUR500"), ("выдп", "wd", "EUR500")])
def test_cash_command_currency_and_sign(command, direction, currency):
    parsed = WalletCommandParser.parse_partner_cash_change(f"/{command}@skyex_bot 1000/2 ! касса")
    assert parsed is not None
    assert parsed.code == currency
    assert parsed.amount == (Decimal("500") if direction == "dep" else Decimal("-500"))
    assert parsed.extra_comment == "касса"
    assert not parsed.is_city_cash


@pytest.mark.parametrize("text,code,amount", [
    ("/деп 1000 RUB", "RUB", "1000"),
    ("/выд 100 USDT", "USDT", "-100"),
    ("/деп THB 1000/2", "THB", "500"),
    ("/выд 12,5 евро", "EUR", "-12.5"),
    ("/деп 100", "RUB", "100"),
    ("/выд 100", "RUB", "-100"),
])
def test_explicit_currency_cash_commands(text, code, amount):
    parsed = WalletCommandParser.parse_partner_cash_change(text)
    assert parsed is not None
    assert (parsed.code, parsed.amount) == (code, Decimal(amount))


@pytest.mark.parametrize("text", ["/депр", "/выдт -100", "/депд 0", "/выде bad", "/деп 100 XYZ"])
def test_invalid_cash_commands_are_rejected(text):
    with pytest.raises(ValueError):
        WalletCommandParser.parse_partner_cash_change(text)


@pytest.mark.parametrize("text", ["/RUB 100", "/пр 2000 от 2000/83", "/депозит 100"])
def test_other_commands_are_not_cash_changes(text):
    assert WalletCommandParser.parse_partner_cash_change(text) is None


def wallet_handler():
    interaction = MagicMock()
    interaction.wallet_service.apply_external_currency_change = AsyncMock()
    handler = WalletsHandler(
        MagicMock(), interaction_service=interaction,
        city_cash_media_store=CityCashMediaStore(), chat_locks=ChatLockRegistry(),
        silent_chat_ids={-200},
    )
    return handler, interaction


@pytest.mark.parametrize("text,code,amount", [
    ("/депр 2000", "RUB", "2000"),
    ("/выдт 100", "USDT", "-100"),
    ("/выд 500 THB", "THB", "-500"),
])
async def test_cash_commands_use_wallet_ledger_without_replies_or_requests(text, code, amount):
    handler, interaction = wallet_handler()
    middleware = SilentAccountingChatsMiddleware(
        {-200}, start_handler=AsyncMock(), wallet_change_handler=handler.on_currency_change,
    )
    downstream = AsyncMock()
    # Real unbound Message: any Telegram reply would fail this test.
    await middleware(downstream, _message(text), {})
    interaction.wallet_service.apply_external_currency_change.assert_awaited_once_with(
        chat_id=-200, chat_name="Поэты", code=code, amount=Decimal(amount), expr=text,
        extra_comment="", source="partner_chat_command", idempotency_key="-200:10",
    )
    interaction.build_currency_change_response.assert_not_called()
    downstream.assert_not_awaited()
    handler.repo.is_manager.assert_not_called()


async def test_cash_commands_do_not_override_normal_chat_flow():
    handler, interaction = wallet_handler()
    middleware = SilentAccountingChatsMiddleware(
        {-200}, start_handler=AsyncMock(), wallet_change_handler=handler.on_currency_change,
    )
    downstream = AsyncMock()
    await middleware(downstream, _message("/депр 2000", chat_id=-300), {})
    downstream.assert_awaited_once()
    interaction.wallet_service.apply_external_currency_change.assert_not_awaited()


async def test_bot_cash_commands_are_ignored():
    handler, interaction = wallet_handler()
    message = _message("/депр 2000").model_copy(update={
        "from_user": User(id=42, is_bot=True, first_name="Other bot"),
    })
    await handler.on_currency_change(message)
    interaction.wallet_service.apply_external_currency_change.assert_not_awaited()


async def test_invalid_cash_command_does_not_post_or_reply():
    handler, interaction = wallet_handler()
    await handler.on_currency_change(_message("/выдт -100"))
    interaction.wallet_service.apply_external_currency_change.assert_not_awaited()
    interaction.build_currency_change_response.assert_not_called()
