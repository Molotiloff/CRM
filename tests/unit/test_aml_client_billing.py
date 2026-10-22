from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from handlers.aml import AMLHandler
from services.aml import AMLQueueFullError

COMMAND = "/амл TEsHvKtA74RJiHHXHdKVmjdEBaxy33CdK9"


def setup(*, manager=False, wallet=7, chat_type="group"):
    repo = SimpleNamespace(is_manager=AsyncMock(return_value=manager))
    billing = SimpleNamespace(wallet_client=AsyncMock(return_value=wallet), charge=AsyncMock(return_value=Decimal("-1")))
    queue = SimpleNamespace(enqueue=AsyncMock(return_value=1))
    handler = AMLHandler(repo, aml_queue_service=queue, billing=billing, admin_chat_ids=[-100])
    bot = Mock(spec=Bot)
    bot.edit_message_text = AsyncMock()
    bot.send_message = AsyncMock()
    bot.send_document = AsyncMock()
    bot.send_photo = AsyncMock()
    bot.delete_message = AsyncMock()
    wait = SimpleNamespace(message_id=50, edit_text=AsyncMock())
    message = Mock(spec=Message)
    message.chat = SimpleNamespace(id=-100, type=chat_type)
    message.from_user = SimpleNamespace(id=42, is_bot=False)
    message.sender_chat = None
    message.text = COMMAND
    message.bot = bot
    message.message_id = 30
    message.answer = AsyncMock(return_value=wait)
    message.edit_text = AsyncMock()
    return handler, message, billing, queue, bot


def callback(handler, message, *, user_id=42, action="yes"):
    token = next(iter(handler._pending))
    query = Mock(spec=CallbackQuery)
    query.data = f"aml:{action}:{token}"
    query.message = message
    query.from_user = SimpleNamespace(id=user_id)
    query.answer = AsyncMock()
    return query


@pytest.mark.parametrize("manager", [True, False])
async def test_private_aml_is_forbidden_even_for_managers(manager):
    handler, message, billing, queue, _ = setup(manager=manager, chat_type="private")
    await handler._cmd_aml(message)
    queue.enqueue.assert_not_awaited()
    billing.wallet_client.assert_not_awaited()
    assert "не в личных" in message.answer.call_args.args[0]


async def test_client_requires_existing_wallet_without_creating_it():
    handler, message, billing, queue, _ = setup(wallet=None)
    await handler._cmd_aml(message)
    queue.enqueue.assert_not_awaited()
    billing.charge.assert_not_awaited()
    assert not handler._pending


async def test_client_in_admin_chat_still_requires_confirmation_and_pays_once():
    handler, message, billing, queue, bot = setup()
    await handler._cmd_aml(message)
    queue.enqueue.assert_not_awaited()
    assert "1 USDT" in message.answer.call_args.args[0]
    query = callback(handler, message)
    await asyncio.gather(handler._confirm(query), handler._confirm(query))
    queue.enqueue.assert_awaited_once()
    billing.charge.assert_not_awaited()
    task = queue.enqueue.call_args.args[0]
    await task.on_success({"message_text": "report", "report_data": {"risk_percent": "0%"}})
    billing.charge.assert_awaited_once_with(client_id=7, chat_id=-100, idempotency_key="aml:-100:30")
    assert "Баланс: -1.00 USDT" in bot.send_message.call_args.args[1]


async def test_other_client_cannot_confirm_or_cancel():
    handler, message, billing, queue, _ = setup()
    await handler._cmd_aml(message)
    query = callback(handler, message, user_id=99)
    await handler._confirm(query)
    assert len(handler._pending) == 1
    queue.enqueue.assert_not_awaited()
    billing.charge.assert_not_awaited()
    assert query.answer.call_args.kwargs["show_alert"]


async def test_cancel_does_not_charge_or_check():
    handler, message, billing, queue, _ = setup()
    await handler._cmd_aml(message)
    await handler._confirm(callback(handler, message, action="no"))
    assert not handler._pending
    queue.enqueue.assert_not_awaited()
    billing.charge.assert_not_awaited()


async def test_manager_in_group_uses_free_flow():
    handler, message, billing, queue, _ = setup(manager=True)
    await handler._cmd_aml(message)
    task = queue.enqueue.call_args.args[0]
    await task.on_success({"message_text": "report", "report_data": {"risk_percent": "0%"}})
    billing.charge.assert_not_awaited()
    assert not handler._pending


async def test_full_queue_and_provider_error_do_not_charge():
    handler, message, billing, queue, _ = setup()
    await handler._cmd_aml(message)
    queue.enqueue.side_effect = AMLQueueFullError()
    await handler._confirm(callback(handler, message))
    billing.charge.assert_not_awaited()
    task = queue.enqueue.call_args.args[0]
    await task.on_error(RuntimeError("provider failed"))
    billing.charge.assert_not_awaited()


async def test_expired_confirmation_and_closed_wallet_are_rejected():
    handler, message, billing, queue, _ = setup()
    await handler._cmd_aml(message)
    query = callback(handler, message)
    billing.wallet_client.return_value = None
    await handler._confirm(query)
    queue.enqueue.assert_not_awaited()
    billing.charge.assert_not_awaited()
    await handler._confirm(query)
    assert query.answer.call_args.kwargs["show_alert"]


async def test_expired_confirmation_does_not_enqueue():
    handler, message, billing, queue, _ = setup()
    await handler._cmd_aml(message)
    query = callback(handler, message)
    token = next(iter(handler._pending))
    handler._pending[token] = replace(handler._pending[token], expires_at=0)
    await handler._confirm(query)
    assert not handler._pending
    queue.enqueue.assert_not_awaited()
    billing.charge.assert_not_awaited()


async def test_image_upload_is_in_memory_and_precedes_billing():
    handler, message, billing, queue, bot = setup()
    await handler._cmd_aml(message)
    await handler._confirm(callback(handler, message))
    uploaded = []

    async def upload(**kwargs):
        billing.charge.assert_not_awaited()
        document = kwargs["photo"]
        assert document.data.startswith(b"\x89PNG")
        assert document.filename.endswith(".png")
        uploaded.append(document)

    bot.send_photo.side_effect = upload
    await queue.enqueue.call_args.args[0].on_success({"report_data": {"risk_percent": "50%"}})
    assert uploaded[0].data == b""
    billing.charge.assert_awaited_once()
    assert "getblock.net" not in bot.send_photo.call_args.kwargs["caption"]
    bot.send_document.assert_not_awaited()
    bot.delete_message.assert_awaited_once_with(chat_id=-100, message_id=50)
    bot.edit_message_text.assert_not_awaited()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_failed_or_cancelled_upload_releases_image_and_does_not_charge(cancelled):
    handler, message, billing, queue, bot = setup()
    await handler._cmd_aml(message)
    await handler._confirm(callback(handler, message))
    uploaded = []

    async def fail(**kwargs):
        uploaded.append(kwargs["photo"])
        raise asyncio.CancelledError() if cancelled else RuntimeError("Telegram unavailable")

    bot.send_photo.side_effect = fail
    error = asyncio.CancelledError if cancelled else RuntimeError
    with pytest.raises(error):
        await queue.enqueue.call_args.args[0].on_success({"report_data": {"risk_percent": "0%"}})
    assert uploaded[0].data == b""
    billing.charge.assert_not_awaited()


async def test_render_failure_does_not_upload_or_charge():
    handler, message, billing, queue, bot = setup()
    handler._pdf_builder = Mock()
    handler._pdf_builder.build.side_effect = RuntimeError("renderer failed")
    await handler._cmd_aml(message)
    await handler._confirm(callback(handler, message))
    with pytest.raises(RuntimeError, match="renderer failed"):
        await queue.enqueue.call_args.args[0].on_success({"report_data": {}})
    bot.send_document.assert_not_awaited()
    bot.send_photo.assert_not_awaited()
    billing.charge.assert_not_awaited()


async def test_pdf_and_message_include_same_checked_wallet_balance_and_risk_sources():
    import io

    from pypdf import PdfReader

    handler, message, billing, queue, bot = setup(manager=True)
    await handler._cmd_aml(message)
    pdf_text = []

    original_builder = handler._pdf_builder

    def capture_pdf(*args, **kwargs):
        data = original_builder.build(*args, **kwargs)
        reader = PdfReader(io.BytesIO(data))
        pdf_text.extend(page.extract_text() for page in reader.pages)
        return data

    handler._pdf_builder = Mock()
    handler._pdf_builder.build.side_effect = capture_pdf
    await queue.enqueue.call_args.args[0].on_success({
        "wallet_balance": "0.000000 USDT",
        "report_data": {
            "asset_name": "USDT", "hash": "TJZY918ginJqvH9RfCEbzdKJv7TtXq553r",
            "risk_percent": "51.2449%", "risk_emoji": "🟡", "risk_label_ru": "Средний уровень риска",
            "trusted_sources": ["Exchange Licensed 70.98%"],
            "suspicious_sources": ["ATM 0.01%"],
            "dangerous_sources": ["Sanctions 0.05%"],
            "preview_link": "https://getblock.net/en/report-preview/test",
        },
    })
    text = bot.send_photo.call_args.kwargs["caption"]
    assert "На балансе кошелька: 0.000000 USDT" in text
    assert all(source in text for source in ("Exchange Licensed 70.98%", "ATM 0.01%", "Sanctions 0.05%"))
    assert "getblock.net" not in text
    assert "PDF" not in text
    assert text.endswith("Отчет")
    assert bot.send_photo.call_args.kwargs["caption_entities"][0].url == "https://getblock.net/en/report-preview/test"
    bot.send_document.assert_not_awaited()
    assert "Остаток USDT" in pdf_text[0]
    assert "0.000000 USDT" in pdf_text[0]
    billing.charge.assert_not_awaited()


async def test_very_long_report_is_sent_completely_without_truncating_sources():
    handler, message, _, queue, bot = setup(manager=True)
    await handler._cmd_aml(message)
    handler._pdf_builder = Mock()
    handler._pdf_builder.build.return_value = b"%PDF-test"
    from unittest.mock import patch

    sources = [f"Источник{i:04} 🟡 0.01%" for i in range(500)]
    with patch("handlers.aml.render_report_page", return_value=(b"png", 1)):
        await queue.enqueue.call_args.args[0].on_success({"report_data": {"trusted_sources": sources, "preview_link": "https://getblock.net/en/report-preview/long"}})
    chunks = [bot.send_photo.call_args.kwargs["caption"], *[call.args[1] for call in bot.send_message.call_args_list]]
    assert len(chunks[0].encode("utf-16-le")) // 2 <= 1024
    assert len(chunks) > 1
    text = "".join(chunks)
    assert all(source in text for source in sources)
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 4096 for chunk in chunks)
    assert bot.send_message.call_args.kwargs["entities"][0].url == "https://getblock.net/en/report-preview/long"


@pytest.mark.parametrize("cancelled", [False, True])
async def test_image_upload_failure_or_cancel_releases_all_buffers_without_charge(cancelled):
    handler, message, billing, queue, bot = setup()
    await handler._cmd_aml(message)
    await handler._confirm(callback(handler, message))
    documents = []
    photos = []

    async def document(**kwargs):
        documents.append(kwargs["document"])

    async def photo(**kwargs):
        photos.append(kwargs["photo"])
        assert photos[-1].data.startswith(b"\x89PNG")
        raise asyncio.CancelledError() if cancelled else RuntimeError("photo upload failed")

    bot.send_document.side_effect = document
    bot.send_photo.side_effect = photo
    with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
        await queue.enqueue.call_args.args[0].on_success({"report_data": {}})
    assert not documents
    bot.send_document.assert_not_awaited()
    assert photos[0].data == b""
    billing.charge.assert_not_awaited()


async def test_all_preview_pages_upload_before_single_billing():
    from unittest.mock import patch

    handler, message, billing, queue, bot = setup()
    await handler._cmd_aml(message)
    await handler._confirm(callback(handler, message))
    photos = []

    async def photo(**kwargs):
        billing.charge.assert_not_awaited()
        photos.append(kwargs["photo"])

    bot.send_photo.side_effect = photo
    with patch("handlers.aml.render_report_page", return_value=(b"png", 3)) as render:
        await queue.enqueue.call_args.args[0].on_success({"report_data": {}})
    assert [call.args[1] for call in render.call_args_list] == [0, 1, 2]
    assert bot.send_photo.await_count == 3
    assert all(photo.data == b"" for photo in photos)
    billing.charge.assert_awaited_once()
