from __future__ import annotations

import asyncio
from types import SimpleNamespace

from db_asyncpg.repositories.client_invite_links import ClientInviteTarget
from services.admin_client.invite_link_backfill import (
    ClientChatLinkInfo,
    ClientInviteLinkBackfill,
    InviteLinkUnavailable,
)
from telegram_adapters.client_invite_links import AiogramClientInviteLinkGateway


class FakeRepository:
    def __init__(self, targets: list[ClientInviteTarget]) -> None:
        self.targets = targets
        self.links: dict[int, str] = {}

    async def list_missing(
        self, *, after_client_id: int, limit: int
    ) -> list[ClientInviteTarget]:
        return [
            target
            for target in self.targets
            if target.client_id > after_client_id and target.client_id not in self.links
        ][:limit]

    async def save_if_missing(self, target: ClientInviteTarget, link: str) -> bool:
        if target.client_id in self.links:
            return False
        self.links[target.client_id] = link
        return True


class FakeGateway:
    def __init__(self, *, username: str | None = None) -> None:
        self.username = username
        self.created: list[int] = []

    async def get_chat_info(self, chat_id: int) -> ClientChatLinkInfo:
        return ClientChatLinkInfo(
            is_group=True,
            username=self.username,
        )

    async def create_approval_link(self, chat_id: int) -> str:
        self.created.append(chat_id)
        return "https://t.me/+approval_link"


async def test_private_chat_gets_separate_approval_link_not_primary_link() -> None:
    target = ClientInviteTarget(client_id=7, chat_id=-100123)
    repo = FakeRepository([target])
    gateway = FakeGateway()
    worker = ClientInviteLinkBackfill(
        gateway=gateway,
        repository=repo,
        per_chat_delay_seconds=0,
    )

    assert await worker.run_once() == 1
    assert repo.links == {7: "https://t.me/+approval_link"}
    assert gateway.created == [-100123]
    assert await worker.run_once() == 0
    assert len(gateway.created) == 1


async def test_public_chat_uses_username_without_creating_invite() -> None:
    target = ClientInviteTarget(client_id=8, chat_id=-100456)
    repo = FakeRepository([target])
    gateway = FakeGateway(username="skyex_public")
    worker = ClientInviteLinkBackfill(
        gateway=gateway,
        repository=repo,
        per_chat_delay_seconds=0,
    )

    assert await worker.run_once() == 1
    assert repo.links == {8: "https://t.me/skyex_public"}
    assert gateway.created == []


async def test_adapter_creates_join_request_link_without_member_limit() -> None:
    class FakeAiogramBot:
        async def create_chat_invite_link(
            self, chat_id: int, **kwargs: object
        ) -> SimpleNamespace:
            assert chat_id == -100123
            assert kwargs == {
                "name": "SkyEx CRM approval",
                "creates_join_request": True,
                "request_timeout": 10,
            }
            return SimpleNamespace(invite_link="https://t.me/+approval_link")

    gateway = AiogramClientInviteLinkGateway(FakeAiogramBot())  # type: ignore[arg-type]
    assert await gateway.create_approval_link(-100123) == "https://t.me/+approval_link"


async def test_unavailable_chat_does_not_stop_other_clients() -> None:
    class PartiallyAvailableGateway(FakeGateway):
        async def get_chat_info(self, chat_id: int) -> ClientChatLinkInfo:
            if chat_id == -100111:
                raise InviteLinkUnavailable("bot is not an administrator")
            return await super().get_chat_info(chat_id)

    repo = FakeRepository(
        [
            ClientInviteTarget(client_id=1, chat_id=-100111),
            ClientInviteTarget(client_id=2, chat_id=-100222),
        ]
    )
    worker = ClientInviteLinkBackfill(
        gateway=PartiallyAvailableGateway(),
        repository=repo,
        per_chat_delay_seconds=0,
    )
    assert await worker.run_once() == 1
    assert repo.links == {2: "https://t.me/+approval_link"}


async def test_background_start_does_not_wait_for_telegram() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowGateway(FakeGateway):
        async def get_chat_info(self, chat_id: int) -> ClientChatLinkInfo:
            started.set()
            await release.wait()
            return await super().get_chat_info(chat_id)

    repo = FakeRepository([ClientInviteTarget(client_id=9, chat_id=-100789)])
    worker = ClientInviteLinkBackfill(
        gateway=SlowGateway(),
        repository=repo,
        per_chat_delay_seconds=0,
    )
    await worker.start()
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert worker.running
    finally:
        await worker.stop()
        release.set()
