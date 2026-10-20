from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import Chat, Message

from handlers.debug import cmd_chat


@pytest.mark.parametrize(
    ("chat", "expected"),
    [
        (Chat(id=-1001, type="group", title="SkyEx | Тай"), "<code>SkyEx | Тай</code>"),
        (Chat(id=-1002, type="supergroup", title="A & B <test>"), "<code>A &amp; B &lt;test&gt;</code>"),
        (Chat(id=42, type="private", first_name="Иван", last_name="Иванов"), "<code>Иван Иванов</code>"),
    ],
)
async def test_chat_command_returns_only_escaped_chat_name(chat, expected):
    message = Mock(spec=Message)
    message.chat = chat
    message.answer = AsyncMock()
    await cmd_chat(message)
    message.answer.assert_awaited_once_with(expected, parse_mode="HTML")
