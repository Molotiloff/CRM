from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import Chat, Message

from handlers.usdt_wallet import UsdtWalletHandler
from services.admin_client.usdt_wallet_service import (
    SECOND_SETTING_KEY,
    SETTING_KEY,
    THAI_SETTING_KEY,
    UsdtWalletService,
)

FIRST = "TEsHvKtA74RJiHHXHdKVmjdEBaxy33CdK9"
SECOND = "TCLUeRdTiyzEcMQ5UVDV6z4ETSreUVcPAS"
THAI = "TJZY918ginJqvH9RfCEbzdKJv7TtXq553r"


class Settings:
    def __init__(self):
        self.values = {}

    async def get_setting(self, key):
        return self.values.get(key)

    async def set_setting(self, key, value):
        self.values[key] = value


async def test_wallets_are_independent_and_use_existing_first_key():
    settings = Settings()
    service = UsdtWalletService(settings)
    await service.set_from_text(f"/setwallet {FIRST}")
    await service.set_from_text(f"/setwallet2 {SECOND}", wallet_slot=2)
    assert settings.values == {SETTING_KEY: FIRST, SECOND_SETTING_KEY: SECOND}
    assert await service.build_show_message() == f"<code>{FIRST}</code>"
    assert await service.build_show_message(wallet_slot=2) == f"<code>{SECOND}</code>"
    # A new service instance reads the same stored values, not per-instance state.
    assert await UsdtWalletService(settings).build_show_message(wallet_slot=2) == f"<code>{SECOND}</code>"
    await service.set_from_text(f"/setwallet2 {FIRST}", wallet_slot=2)
    assert settings.values[SETTING_KEY] == FIRST
    assert settings.values[SECOND_SETTING_KEY] == FIRST


async def test_missing_second_wallet_does_not_fall_back_to_first():
    settings = Settings()
    settings.values[SETTING_KEY] = FIRST
    service = UsdtWalletService(settings)
    assert await service.build_show_message(wallet_slot=2) == "Второй USDT-кошелёк пока не задан."


@pytest.mark.parametrize("text", ["/setwallet2", "/setwallet2 short"])
async def test_invalid_second_wallet_does_not_overwrite_settings(text):
    settings = Settings()
    settings.values.update({SETTING_KEY: FIRST, SECOND_SETTING_KEY: SECOND})
    await UsdtWalletService(settings).set_from_text(text, wallet_slot=2)
    assert settings.values == {SETTING_KEY: FIRST, SECOND_SETTING_KEY: SECOND}


@pytest.mark.parametrize("slot", [1, 2])
async def test_both_set_commands_have_same_admin_chat_restriction(slot):
    settings = Settings()
    handler = UsdtWalletHandler(UsdtWalletService(settings), admin_chat_ids={-1001})
    message = Mock(spec=Message)
    message.chat = SimpleNamespace(id=-1002)
    message.text = f"/setwallet2 {SECOND}" if slot == 2 else f"/setwallet {FIRST}"
    message.answer = AsyncMock()
    command = handler._cmd_set2 if slot == 2 else handler._cmd_set
    await command(message)
    assert not settings.values
    message.chat.id = -1001
    await command(message)
    assert settings.values == ({SECOND_SETTING_KEY: SECOND} if slot == 2 else {SETTING_KEY: FIRST})


async def test_show_second_wallet_is_public_and_html_escaped():
    settings = Settings()
    settings.values[SECOND_SETTING_KEY] = "<address&>"
    handler = UsdtWalletHandler(UsdtWalletService(settings), admin_chat_ids={-1001})
    message = Mock(spec=Message)
    message.answer = AsyncMock()
    await handler._cmd_show2(message)
    message.answer.assert_awaited_once_with("<code>&lt;address&amp;&gt;</code>", parse_mode="HTML")


async def test_thai_wallet_is_independent_and_read_from_persistent_settings():
    settings = Settings()
    settings.values.update({SETTING_KEY: FIRST, SECOND_SETTING_KEY: SECOND})
    service = UsdtWalletService(settings)
    assert await service.set_from_text(f"/setwalletthai {THAI}", wallet_slot="thai") == "✅ Тайский USDT-кошелёк обновлён."
    assert settings.values == {SETTING_KEY: FIRST, SECOND_SETTING_KEY: SECOND, THAI_SETTING_KEY: THAI}
    assert await UsdtWalletService(settings).build_show_message(wallet_slot="thai") == f"<code>{THAI}</code>"
    await service.set_from_text(f"/setwallett {SECOND}", wallet_slot="thai")
    assert await service.build_show_message() == f"<code>{FIRST}</code>"
    assert await service.build_show_message(wallet_slot=2) == f"<code>{SECOND}</code>"
    assert await service.build_show_message(wallet_slot="thai") == f"<code>{SECOND}</code>"


async def test_missing_thai_wallet_does_not_show_first_or_second_wallet():
    settings = Settings()
    settings.values.update({SETTING_KEY: FIRST, SECOND_SETTING_KEY: SECOND})
    assert await UsdtWalletService(settings).build_show_message(wallet_slot="thai") == "Тайский USDT-кошелёк пока не задан."


@pytest.mark.parametrize("text", ["/setwalletthai", "/setwallett", "/setwalletthai short", "/setwallett " + "a" * 129])
async def test_invalid_thai_address_never_overwrites_any_wallet(text):
    settings = Settings()
    expected = {SETTING_KEY: FIRST, SECOND_SETTING_KEY: SECOND, THAI_SETTING_KEY: THAI}
    settings.values.update(expected)
    await UsdtWalletService(settings).set_from_text(text, wallet_slot="thai")
    assert settings.values == expected


async def test_thai_set_is_restricted_to_admin_chat_and_show_is_public():
    settings = Settings()
    handler = UsdtWalletHandler(UsdtWalletService(settings), admin_chat_ids={-1001})
    message = Mock(spec=Message)
    message.chat = SimpleNamespace(id=-1002)
    message.text = f"/setwallett {THAI}"
    message.answer = AsyncMock()
    await handler._cmd_set_thai(message)
    assert settings.values == {}
    message.chat.id = -1001
    await handler._cmd_set_thai(message)
    assert settings.values == {THAI_SETTING_KEY: THAI}
    message.chat.id = -1002
    message.answer.reset_mock()
    await handler._cmd_show_thai(message)
    message.answer.assert_awaited_once_with(f"<code>{THAI}</code>", parse_mode="HTML")


async def test_thai_wallet_is_html_escaped():
    settings = Settings()
    settings.values[THAI_SETTING_KEY] = "<thai&wallet>"
    assert await UsdtWalletService(settings).build_show_message(wallet_slot="thai") == "<code>&lt;thai&amp;wallet&gt;</code>"


@pytest.mark.parametrize("command,callback", [
    ("кош", "_cmd_show"), ("кош2", "_cmd_show2"),
    ("коштай", "_cmd_show_thai"), ("кошт", "_cmd_show_thai"),
    ("setwallet", "_cmd_set"), ("setwallet2", "_cmd_set2"),
    ("setwalletthai", "_cmd_set_thai"), ("setwallett", "_cmd_set_thai"),
])
@pytest.mark.parametrize("mention", ["", "@SkyExTestBot"])
async def test_wallet_commands_and_aliases_route_to_one_correct_handler(command, callback, mention):
    handler = UsdtWalletHandler(UsdtWalletService(Settings()), admin_chat_ids={-1001})
    message = Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=-1001, type="supergroup"), text=f"/{command}{mention} {THAI}")
    bot = SimpleNamespace(me=AsyncMock(return_value=SimpleNamespace(username="SkyExTestBot")))
    matched = []
    for registered in handler.router.message.handlers:
        if (await registered.check(message, bot=bot))[0]:
            matched.append(registered.callback.__name__)
    assert matched == [callback]
