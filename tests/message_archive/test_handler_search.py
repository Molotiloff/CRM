from __future__ import annotations

import asyncio
from types import SimpleNamespace

from handlers.message_archive import MessageArchiveHandler


class _ArchiveRepo:
    async def search_archive_chats(self, query: str, *, limit: int = 20) -> list[dict]:
        assert query == "SKYEX | Алик Мануйлов"
        assert limit == 20
        return []


class _ClientRepo:
    def __init__(self, client: dict | None) -> None:
        self.client = client

    async def find_client_by_name_exact(self, name: str) -> dict | None:
        assert name == "SKYEX | Алик Мануйлов"
        return self.client


class _ManagerRepo:
    async def is_manager(self, user_id: int) -> bool:
        raise AssertionError("Admin user must not require a manager lookup")


class _Message:
    def __init__(self) -> None:
        self.text = "/сообщение SKYEX | Алик Мануйлов"
        self.chat = SimpleNamespace(id=-1001)
        self.from_user = SimpleNamespace(id=42)
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs) -> None:
        del kwargs
        self.answers.append(text)


def _handler(client: dict | None) -> MessageArchiveHandler:
    return MessageArchiveHandler(
        bot=object(),  # type: ignore[arg-type]
        repo=_ArchiveRepo(),  # type: ignore[arg-type]
        client_repo=_ClientRepo(client),  # type: ignore[arg-type]
        manager_repo=_ManagerRepo(),  # type: ignore[arg-type]
        export_service=object(),  # type: ignore[arg-type]
        admin_chat_id=-1001,
        admin_user_ids={42},
    )


def test_command_explains_when_client_exists_but_archive_is_empty() -> None:
    message = _Message()
    handler = _handler(
        {
            "name": "SKYEX | Алик Мануйлов",
            "chat_id": -4896204473,
        }
    )

    asyncio.run(handler._command(message))

    assert len(message.answers) == 1
    assert "chat_id=-4896204473" in message.answers[0]
    assert "сохранённых сообщений" in message.answers[0]
    assert "импорта старой истории" in message.answers[0]


def test_command_distinguishes_unknown_chat_from_empty_archive() -> None:
    message = _Message()
    handler = _handler(None)

    asyncio.run(handler._command(message))

    assert message.answers == [
        "Чат «SKYEX | Алик Мануйлов» в архиве и списке клиентов не найден."
    ]


