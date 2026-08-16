from __future__ import annotations

from decimal import Decimal

from services.rate_order import (
    LiveMessageEditResult,
    LiveMessageEditStatus,
    OrderbookService,
)


class OrderbookStub:
    def get_asks(self) -> list[dict[str, Decimal]]:
        return [{"price": Decimal("80"), "volume": Decimal("500000")}]

    def get_bids(self) -> list[dict[str, Decimal]]:
        return [{"price": Decimal("79"), "volume": Decimal("500000")}]


class LiveMessageRepoStub:
    def __init__(self) -> None:
        self.binding: tuple[int, str, int] | None = None
        self.deleted: tuple[int, str] | None = None

    async def upsert_live_message(
        self,
        *,
        chat_id: int,
        message_key: str,
        message_id: int,
    ) -> None:
        self.binding = (chat_id, message_key, message_id)

    async def delete_live_message(self, *, chat_id: int, message_key: str) -> None:
        self.deleted = (chat_id, message_key)


class LiveMessageEditorStub:
    def __init__(self, status: LiveMessageEditStatus) -> None:
        self.status = status
        self.calls: list[tuple[int, int, str]] = []

    async def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> LiveMessageEditResult:
        self.calls.append((chat_id, message_id, text))
        return LiveMessageEditResult(self.status)


def build_service(
    *,
    editor: LiveMessageEditorStub,
    repo: LiveMessageRepoStub,
) -> OrderbookService:
    service = OrderbookService(
        ws_service=OrderbookStub(),
        repo=repo,
        live_message_editor=editor,
        exchange_name="Test exchange",
    )
    service.MIN_REFRESH_INTERVAL_SECONDS = 0
    return service


async def test_refresh_live_message_uses_editor_port() -> None:
    repo = LiveMessageRepoStub()
    editor = LiveMessageEditorStub(LiveMessageEditStatus.UPDATED)
    service = build_service(editor=editor, repo=repo)
    await service.set_live_message(chat_id=10, message_id=20)

    await service.refresh_live_message()

    assert len(editor.calls) == 1
    assert editor.calls[0][0:2] == (10, 20)
    assert "USDT/RUB" in editor.calls[0][2]
    assert repo.deleted is None


async def test_missing_live_message_clears_persisted_binding() -> None:
    repo = LiveMessageRepoStub()
    editor = LiveMessageEditorStub(LiveMessageEditStatus.MISSING)
    service = build_service(editor=editor, repo=repo)
    await service.set_live_message(chat_id=10, message_id=20)

    await service.refresh_live_message()

    assert repo.deleted == (10, service.LIVE_MESSAGE_KEY)
    assert service._live_chat_id is None
    assert service._live_message_id is None
