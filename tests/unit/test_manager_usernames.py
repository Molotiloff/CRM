from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChat

from handlers.managers import ManagersHandler
from services.admin_client.manager_admin_service import ManagerAdminService


async def test_manager_usernames_live_missing_and_cached():
    rows = [
        {"user_id": 1, "display_name": "<Staff>", "username": "old"},
        {"user_id": 2, "username": "removed"},
        {"user_id": 3, "username": "cached"},
        {"user_id": 4},
        {"user_id": 5, "username": "<unsafe>"},
    ]
    profiles = {1: {"username": "new"}, 2: {"username": None}, 3: None, 4: None, 5: None}

    async def resolve(uid):
        return profiles[uid]

    service = ManagerAdminService(SimpleNamespace(list_managers=AsyncMock(return_value=rows)))
    result = await service.handle_command("/mgr", profile_resolver=resolve)
    assert result == (
        "Менеджеры:\n• <code>1</code> — @new\n• <code>2</code> — ник не указан"
        "\n• <code>3</code> — @cached (последний известный)"
        "\n• <code>4</code> — ник недоступен\n• <code>5</code> — ник недоступен"
    )


@pytest.mark.parametrize("operation, verb", [("+", "Добавлен"), ("-", "Удалён")])
async def test_manager_change_acknowledgement(operation, verb):
    repo = SimpleNamespace(
        list_managers=AsyncMock(return_value=[{"user_id": 777}]),
        add_manager=AsyncMock(return_value=True),
        remove_manager=AsyncMock(return_value=True),
    )
    resolver = AsyncMock(return_value={"username": "staff_nick"})
    result = await ManagerAdminService(repo).handle_command(
        f"/mgr {operation} 777",
        profile_resolver=resolver,
    )
    assert result == f"✅ {verb} менеджер: <code>777</code> — @staff_nick"


@pytest.mark.parametrize("available", [True, False])
async def test_handler_falls_back_to_group_then_archive(available):
    repo = SimpleNamespace(
        list_managers=AsyncMock(
            return_value=[
                {
                    "user_id": 777,
                    "username": "cached",
                }
            ]
        )
    )
    error = TelegramBadRequest(method=GetChat(chat_id=777), message="chat not found")
    bot = SimpleNamespace(
        get_chat=AsyncMock(side_effect=error),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(user=SimpleNamespace(username="live"))
        ),
    )
    if not available:
        bot.get_chat_member.side_effect = error
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-900), bot=bot, text="/mgr", answer=AsyncMock()
    )
    await ManagersHandler(ManagerAdminService(repo), -900)._cmd_mgr(message)
    assert (
        "@live" if available else "@cached (последний известный)"
    ) in message.answer.call_args.args[0]


async def test_handler_denies_other_chats_without_profile_requests():
    service = SimpleNamespace(handle_command=AsyncMock())
    message = SimpleNamespace(chat=SimpleNamespace(id=-1), answer=AsyncMock())
    await ManagersHandler(service, -900)._cmd_mgr(message)
    service.handle_command.assert_not_awaited()
