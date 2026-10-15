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
    await task.on_success({"message_text": "report"})
    billing.charge.assert_awaited_once_with(client_id=7, chat_id=-100, idempotency_key="aml:-100:30")
    assert "Баланс: -1.00 USDT" in bot.edit_message_text.call_args.args[0]


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
    await task.on_success({"message_text": "report"})
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
