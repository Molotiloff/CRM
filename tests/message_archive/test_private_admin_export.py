from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import CallbackQuery, Message

from handlers.message_archive import MessageArchiveHandler


def _setup(*, user_id=42, chat_id=42, chat_type="private", manager=False):
    repo = SimpleNamespace(
        search_archive_chats=AsyncMock(return_value=[{"id": 7, "current_title": "Клиент"}]),
        get_archive_chat=AsyncMock(return_value={"id": 7, "current_title": "Клиент"}),
    )
    managers = SimpleNamespace(is_manager=AsyncMock(return_value=manager))
    handler = MessageArchiveHandler(
        bot=Mock(), repo=repo, client_repo=Mock(), manager_repo=managers,
        export_service=Mock(), admin_chat_id=-1001, admin_user_ids={42},
    )
    handler._start_export = Mock(return_value=True)
    message = Mock(spec=Message)
    message.chat = SimpleNamespace(id=chat_id, type=chat_type)
    message.from_user = SimpleNamespace(id=user_id)
    message.text = "/сообщение Клиент"
    message.answer = AsyncMock()
    message.edit_reply_markup = AsyncMock()
    callback = Mock(spec=CallbackQuery)
    callback.message = message
    callback.from_user = message.from_user
    callback.data = "message_archive:chat:7"
    callback.answer = AsyncMock()
    return handler, repo, managers, message, callback


async def test_admin_can_export_in_private_chat():
    handler, repo, managers, message, _ = _setup()
    await handler._command(message)
    repo.search_archive_chats.assert_awaited_once_with("Клиент", limit=20)
    handler._start_export.assert_called_once_with(
        archive_chat_id=7, requested_by_user_id=42, requested_in_chat_id=42,
    )
    managers.is_manager.assert_not_awaited()


async def test_admin_can_choose_chat_in_private_export():
    handler, _, managers, _, callback = _setup()
    await handler._choose_chat(callback)
    handler._start_export.assert_called_once_with(
        archive_chat_id=7, requested_by_user_id=42, requested_in_chat_id=42,
    )
    managers.is_manager.assert_not_awaited()


@pytest.mark.parametrize("manager", [False, True])
async def test_non_admin_cannot_export_or_choose_in_private_chat(manager):
    handler, repo, managers, message, callback = _setup(user_id=99, chat_id=99, manager=manager)
    await handler._command(message)
    await handler._choose_chat(callback)
    repo.search_archive_chats.assert_not_awaited()
    repo.get_archive_chat.assert_not_awaited()
    handler._start_export.assert_not_called()
    managers.is_manager.assert_not_awaited()
    assert callback.answer.call_args.kwargs["show_alert"]


async def test_admin_cannot_export_from_arbitrary_group():
    handler, repo, _, message, callback = _setup(chat_id=-555, chat_type="supergroup")
    await handler._command(message)
    await handler._choose_chat(callback)
    repo.search_archive_chats.assert_not_awaited()
    handler._start_export.assert_not_called()


async def test_manager_can_still_export_in_admin_group():
    handler, _, managers, message, _ = _setup(user_id=99, chat_id=-1001, chat_type="supergroup", manager=True)
    await handler._command(message)
    managers.is_manager.assert_awaited_once_with(99)
    handler._start_export.assert_called_once_with(
        archive_chat_id=7, requested_by_user_id=99, requested_in_chat_id=-1001,
    )
