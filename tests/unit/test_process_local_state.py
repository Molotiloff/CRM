from __future__ import annotations

import gc

import pytest

from services.request_table.session_store import RequestTableSessionStore
from telegram_adapters import ChatLockRegistry


def test_request_table_session_store_is_scoped_and_bounded() -> None:
    store = RequestTableSessionStore(max_marked=2)

    store.add_pending("done", (1, 10))
    store.mark("done", (1, 10))
    store.mark("delete", (1, 10))
    store.mark("done", (1, 11))

    assert store.is_pending("done", (1, 10)) is True
    assert store.is_marked("done", (1, 10)) is False
    assert store.is_marked("delete", (1, 10)) is True
    assert store.is_marked("done", (1, 11)) is True

    store.discard_pending("done", (1, 10))
    assert store.is_pending("done", (1, 10)) is False


def test_request_table_session_store_rejects_unbounded_configuration() -> None:
    with pytest.raises(ValueError, match="positive"):
        RequestTableSessionStore(max_marked=0)


def test_chat_lock_registry_reuses_active_locks_and_releases_idle_entries() -> None:
    registry = ChatLockRegistry()
    first = registry.for_chat(-100)

    assert registry.for_chat(-100) is first
    assert registry.active_count() == 1

    del first
    gc.collect()

    assert registry.active_count() == 0
