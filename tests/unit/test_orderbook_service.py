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


async def test_exchange_live_bindings_restore_independently():
    class MultiBindingRepo:
        def __init__(self):
            self.bindings = {}

        async def upsert_live_message(self, *, chat_id, message_key, message_id):
            self.bindings[chat_id, message_key] = {"chat_id": chat_id, "message_id": message_id}

        async def get_live_message(self, *, chat_id, message_key):
            return self.bindings.get((chat_id, message_key))

        async def delete_live_message(self, *, chat_id, message_key):
            del self.bindings[chat_id, message_key]

    repo = MultiBindingRepo()
    editor = LiveMessageEditorStub(LiveMessageEditStatus.UPDATED)

    def exchange(key):
        return OrderbookService(
            ws_service=OrderbookStub(), repo=repo, live_message_editor=editor,
            live_message_key=key,
        )

    rapira = exchange("orderbook_live")
    abcex = exchange("orderbook_live_abcex")
    await rapira.set_live_message(chat_id=10, message_id=20)
    await abcex.set_live_message(chat_id=10, message_id=30)
    restored_rapira = exchange("orderbook_live")
    restored_abcex = exchange("orderbook_live_abcex")
    await restored_rapira.restore_live_message(admin_chat_id=10)
    await restored_abcex.restore_live_message(admin_chat_id=10)
    assert restored_rapira._live_message_id == 20
    assert restored_abcex._live_message_id == 30
    await restored_abcex.clear_live_message()
    assert (10, "orderbook_live") in repo.bindings


async def test_burst_updates_use_one_worker_and_stop_cancels_blocked_edit():
    import asyncio

    class BlockingEditor:
        def __init__(self):
            self.entered = asyncio.Event()
            self.cancelled = False
            self.calls = 0

        async def edit_text(self, **kwargs):
            self.calls += 1
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    editor = BlockingEditor()
    service = build_service(editor=editor, repo=LiveMessageRepoStub())
    await service.set_live_message(chat_id=10, message_id=20)
    await service.start()
    worker = service._task
    await service.start()
    assert service._task is worker
    service.request_refresh()
    await asyncio.wait_for(editor.entered.wait(), 1)
    tasks_before = set(asyncio.all_tasks())
    for _ in range(10000):
        service.request_refresh()
    assert set(asyncio.all_tasks()) == tasks_before
    assert editor.calls == 1
    await asyncio.wait_for(service.stop(), 1)
    assert editor.cancelled
    assert worker.done()
    assert not service._refresh_requested.is_set()
    assert service._task is None


async def test_stop_during_retry_after_does_not_respawn_worker():
    import asyncio

    class RetryEditor:
        def __init__(self):
            self.entered = asyncio.Event()

        async def edit_text(self, **kwargs):
            self.entered.set()
            return LiveMessageEditResult(LiveMessageEditStatus.RETRY, retry_after_seconds=3600)

    editor = RetryEditor()
    service = build_service(editor=editor, repo=LiveMessageRepoStub())
    await service.set_live_message(chat_id=10, message_id=20)
    await service.start()
    service.request_refresh()
    await asyncio.wait_for(editor.entered.wait(), 1)
    await asyncio.wait_for(service.stop(), 1)
    assert not any(t.get_name() == service._task_name for t in asyncio.all_tasks())
