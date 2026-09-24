from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import CallbackQuery, Chat, LinkPreviewOptions, Message, User

from handlers.start import SKYEX_INFO_TEXT, StartHandler
from handlers.wallets import WalletsHandler
from middlewares.silent_accounting_chats import SilentAccountingChatsMiddleware
from services.wallets.models import (
    ParsedCurrencyChange,
    PartnerUsdtSendCommand,
    WalletCommandResult,
)
from telegram_adapters import ChatLockRegistry, CityCashMediaStore


def _message(text: str, *, chat_id: int = -200) -> Message:
    return Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="group", title="Поэты"),
        from_user=User(id=42, is_bot=False, first_name="Manager"),
        text=text,
    )


async def test_silent_middleware_routes_only_start_and_wallet_changes() -> None:
    downstream = AsyncMock(return_value="downstream")
    start = AsyncMock(return_value="start")
    wallet = AsyncMock(return_value="wallet")
    middleware = SilentAccountingChatsMiddleware(
        {-200},
        start_handler=start,
        wallet_change_handler=wallet,
    )

    assert await middleware(downstream, _message("/start"), {}) == "start"
    assert await middleware(downstream, _message("/RUB 100"), {}) == "wallet"
    assert await middleware(downstream, _message("/баотпр"), {}) == "wallet"
    assert await middleware(downstream, _message("/дай"), {}) is None
    assert await middleware(downstream, _message("обычное сообщение"), {}) is None
    assert await middleware(downstream, _message("/дай", chat_id=-300), {}) == "downstream"

    assert start.await_count == 1
    assert wallet.await_count == 2
    assert downstream.await_count == 1


async def test_silent_middleware_swallows_callbacks() -> None:
    downstream = AsyncMock()
    middleware = SilentAccountingChatsMiddleware(
        {-200},
        start_handler=AsyncMock(),
        wallet_change_handler=AsyncMock(),
    )
    callback = CallbackQuery(
        id="callback-1",
        from_user=User(id=42, is_bot=False, first_name="Manager"),
        chat_instance="instance",
        message=_message("old bot message"),
        data="undo:RUB:+:100",
    )

    assert await middleware(downstream, callback, {}) is None
    downstream.assert_not_awaited()


async def test_start_registers_silent_chat_without_answer() -> None:
    bootstrap = AsyncMock()
    sync_registry = AsyncMock()
    handler = StartHandler(
        bootstrap,
        silent_chat_ids={-200},
        on_silent_wallet_ready=sync_registry,
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(
            id=-200,
            title="Поэты",
            first_name=None,
            last_name=None,
            username=None,
        ),
        answer=AsyncMock(),
    )

    await handler._on_start(message)

    bootstrap.ensure_client_wallet.assert_awaited_once_with(
        chat_id=-200,
        chat_name="Поэты",
    )
    sync_registry.assert_awaited_once_with()
    message.answer.assert_not_awaited()


async def test_info_sends_single_formatted_message() -> None:
    handler = StartHandler(AsyncMock())
    message = SimpleNamespace(answer=AsyncMock())

    await handler._cmd_info(message)

    message.answer.assert_awaited_once_with(
        SKYEX_INFO_TEXT,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    assert "https://t.me/skyex_support" in SKYEX_INFO_TEXT
    assert "https://sky-ex.ru/" in SKYEX_INFO_TEXT
    assert len(SKYEX_INFO_TEXT) < 4096


async def test_wallet_change_posts_silently() -> None:
    parsed = ParsedCurrencyChange(
        code="RUB",
        expr="100",
        amount=Decimal("100"),
        tail="",
        is_city_cash=False,
        cash_city=None,
        client_name_for_transfer="",
        extra_comment="",
    )
    interaction = MagicMock()
    interaction.wallet_service.parser.parse_partner_usdt_send.return_value = None
    interaction.wallet_service.parse_currency_change.return_value = parsed
    interaction.build_currency_change_response = AsyncMock(
        return_value=WalletCommandResult(ok=True, message_text="recorded")
    )
    repo = MagicMock()
    repo.is_manager = AsyncMock(return_value=True)
    handler = WalletsHandler(
        repo,
        interaction_service=interaction,
        city_cash_media_store=CityCashMediaStore(),
        chat_locks=ChatLockRegistry(),
        silent_chat_ids={-200},
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(
            id=-200,
            title="Поэты",
            first_name=None,
            last_name=None,
            username=None,
        ),
        from_user=SimpleNamespace(id=42),
        bot=SimpleNamespace(id=999),
        text="/RUB 100",
        caption=None,
        photo=None,
        media_group_id=None,
        message_id=10,
        reply_to_message=None,
        answer=AsyncMock(),
    )

    await handler._on_currency_change(message)

    interaction.build_currency_change_response.assert_awaited_once()
    message.answer.assert_not_awaited()


async def test_partner_manager_can_silently_post_usdt_send() -> None:
    interaction = MagicMock()
    interaction.wallet_service.parser.parse_partner_usdt_send.return_value = (
        PartnerUsdtSendCommand(amount=Decimal("50000"), raw_text="/отпр 50000")
    )
    interaction.wallet_service.apply_external_currency_change = AsyncMock()
    repo = MagicMock()
    repo.is_manager = AsyncMock(return_value=False)
    handler = WalletsHandler(
        repo,
        interaction_service=interaction,
        city_cash_media_store=CityCashMediaStore(),
        chat_locks=ChatLockRegistry(),
        silent_chat_ids={-200},
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(
            id=-200,
            title="Поэты",
            first_name=None,
            last_name=None,
            username=None,
        ),
        from_user=SimpleNamespace(id=777),
        bot=SimpleNamespace(id=999),
        text="/отпр 50000",
        caption=None,
        photo=None,
        media_group_id=None,
        message_id=11,
        reply_to_message=None,
        answer=AsyncMock(),
    )

    await handler._on_currency_change(message)

    repo.is_manager.assert_not_awaited()
    interaction.wallet_service.apply_external_currency_change.assert_awaited_once_with(
        chat_id=-200,
        chat_name="Поэты",
        code="USDT",
        source="partner_chat_command",
        idempotency_key="-200:11",
        amount=Decimal("-50000"),
        expr="/отпр 50000",
    )
    message.answer.assert_not_awaited()


async def test_partner_send_all_delegates_to_wallet_zeroing() -> None:
    interaction = MagicMock()
    interaction.wallet_service.parser.parse_partner_usdt_send.return_value = (
        PartnerUsdtSendCommand(amount=None, raw_text="/баотпр")
    )
    interaction.wallet_service.withdraw_all = AsyncMock()
    handler = WalletsHandler(
        MagicMock(),
        interaction_service=interaction,
        city_cash_media_store=CityCashMediaStore(),
        chat_locks=ChatLockRegistry(),
        silent_chat_ids={-200},
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(
            id=-200,
            title="Поэты",
            first_name=None,
            last_name=None,
            username=None,
        ),
        from_user=SimpleNamespace(id=777),
        bot=SimpleNamespace(id=999),
        text="/баотпр",
        caption=None,
        photo=None,
        media_group_id=None,
        message_id=12,
        reply_to_message=None,
        answer=AsyncMock(),
    )

    await handler._on_currency_change(message)

    interaction.wallet_service.withdraw_all.assert_awaited_once_with(
        chat_id=-200,
        chat_name="Поэты",
        code="USDT",
        source="partner_chat_command",
        idempotency_key="-200:12",
        comment="/баотпр",
    )
    message.answer.assert_not_awaited()
