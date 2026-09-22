from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from handlers.wallets import WalletsHandler
from services.accounting.cash_settlement_models import CashSettlementResult
from services.wallets.models import ParsedCurrencyChange
from telegram_adapters.city_cash_transfer import (
    send_cash_settlement_evidence_to_client,
)


def _photo_message(*file_ids: str):
    return SimpleNamespace(
        photo=[SimpleNamespace(file_id=file_id) for file_id in file_ids]
    )


async def test_send_cash_settlement_photo_uses_largest_telegram_size() -> None:
    bot = SimpleNamespace(
        send_photo=AsyncMock(),
        send_media_group=AsyncMock(),
    )

    chat_id = await send_cash_settlement_evidence_to_client(
        repo=MagicMock(),
        bot=bot,
        evidence_messages=[_photo_message("small", "large")],
        target_chat_id=-100500,
        target_client_id=42,
    )

    assert chat_id == -100500
    bot.send_photo.assert_awaited_once_with(chat_id=-100500, photo="large")
    bot.send_media_group.assert_not_awaited()


async def test_send_cash_settlement_album_preserves_all_photos() -> None:
    bot = SimpleNamespace(
        send_photo=AsyncMock(),
        send_media_group=AsyncMock(),
    )

    await send_cash_settlement_evidence_to_client(
        repo=MagicMock(),
        bot=bot,
        evidence_messages=[
            _photo_message("first-small", "first"),
            _photo_message("second-small", "second"),
        ],
        target_chat_id=-100500,
        target_client_id=42,
    )

    bot.send_photo.assert_not_awaited()
    bot.send_media_group.assert_awaited_once()
    call = bot.send_media_group.await_args
    assert call.kwargs["chat_id"] == -100500
    assert [item.media for item in call.kwargs["media"]] == ["first", "second"]


async def test_cash_request_command_sends_photo_and_names_client() -> None:
    repo = MagicMock()
    bot = SimpleNamespace(
        id=999,
        send_photo=AsyncMock(),
        send_media_group=AsyncMock(),
    )
    wallet_service = MagicMock()
    wallet_service.parse_currency_change.return_value = ParsedCurrencyChange(
        code="RUB",
        expr="100000",
        amount=Decimal("100000"),
        tail="Б-2349823",
        is_city_cash=True,
        cash_city="екб",
        client_name_for_transfer="Б-2349823",
        extra_comment="",
    )
    interaction = SimpleNamespace(wallet_service=wallet_service)
    cash_settlements = SimpleNamespace(
        settle=AsyncMock(
            return_value=CashSettlementResult(
                settlement_id=5,
                deal_id=7,
                client_id=42,
                client_chat_id=-100500,
                client_name="SkyEx | Клиент",
                request_id="Б-2349823",
                request_kind="dep",
                currency="RUB",
                actual_qty=Decimal("100000"),
                cash_transaction_id=8,
                client_transaction_id=9,
                position_move_id=None,
                repeated=False,
            )
        )
    )
    handler = WalletsHandler(
        repo=repo,
        interaction_service=interaction,
        city_cash_media_store=MagicMock(),
        chat_locks=MagicMock(),
        city_cash_chats={"екб": -700001},
        cash_settlement_service=cash_settlements,
    )
    message = SimpleNamespace(
        text=None,
        caption="/руб 100000 Б-2349823",
        chat=SimpleNamespace(id=-700001),
        message_id=501,
        media_group_id=None,
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="receipt")],
        from_user=SimpleNamespace(id=77),
        bot=bot,
        answer=AsyncMock(),
    )

    await handler._execute_currency_change(message)

    bot.send_photo.assert_awaited_once_with(chat_id=-100500, photo="receipt")
    message.answer.assert_awaited_once_with(
        "✅ Заявка: Б-2349823 проведена\nКлиент: SkyEx | Клиент"
    )
