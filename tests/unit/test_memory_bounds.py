from __future__ import annotations

import gc
import tracemalloc
from types import SimpleNamespace

import pytest

from services.aml import getblock_parser
from services.aml.aml_queue_service import (
    AMLQueueFullError,
    AMLQueueService,
    AMLQueueTask,
)
from telegram_adapters.broadcast_models import TextBroadcastPayload
from telegram_adapters.broadcast_session_store import AiogramBroadcastSessionStore
from telegram_adapters.city_cash_media_store import CityCashMediaStore


class _Checker:
    async def check_wallet(self, wallet: str) -> dict[str, str]:
        return {"wallet": wallet}


async def _callback(_value: object) -> None:
    return None


async def test_aml_queue_rejects_tasks_when_full() -> None:
    queue = AMLQueueService(checker=_Checker(), max_queue_size=1)
    task = AMLQueueTask(
        wallet="wallet",
        on_success=_callback,
        on_error=_callback,
    )

    assert await queue.enqueue(task) == 1
    with pytest.raises(AMLQueueFullError):
        await queue.enqueue(task)


def test_report_parser_releases_soup_without_waiting_for_cyclic_gc() -> None:
    filler = '<div><span>payload</span><a href="#">link</a></div>' * 200
    report_html = f"""
    <html><body>{filler}<div id="report-info">
      <div class="details-info-item"><p>Blockchain: <span>TRON</span></p></div>
      <div class="details-info-item"><p>Token: <span>USDT</span></p></div>
      <div class="details-info-item"><p>Hash: <span>TXyz</span></p></div>
      <p class="risk-level"><span>12%</span><span>Low risk level</span></p>
    </div></body></html>
    """
    gc.collect()
    gc.disable()
    tracemalloc.start()
    try:
        for index in range(5):
            result = getblock_parser.parse_report_preview(
                report_html,
                str(index),
                base_url="https://example.test",
                lang="en",
            )
            assert result["risk_percent"] == "12%"

        retained_bytes, _peak_bytes = tracemalloc.get_traced_memory()
        assert retained_bytes < 2 * 1024 * 1024
    finally:
        tracemalloc.stop()
        gc.enable()
        gc.collect()


def test_csrf_parser_uses_lightweight_path_for_standard_markup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("BeautifulSoup must not be used for standard CSRF markup")

    monkeypatch.setattr(getblock_parser, "BeautifulSoup", fail_if_called)

    assert (
        getblock_parser.extract_csrf_from_html(
            '<meta name="csrf-token" content="header-token">'
        )
        == "header-token"
    )
    assert (
        getblock_parser.find_hidden_csrf_field(
            '<input type="hidden" name="_csrf" value="form-token">'
        )
        == "form-token"
    )


def test_broadcast_drafts_are_bounded() -> None:
    store = AiogramBroadcastSessionStore(max_pending_items=2)
    payload = TextBroadcastPayload(text="text", entities=(), group=None)
    for message_id in range(1, 4):
        store.add_prompt(
            chat_id=10,
            prompt_message_id=message_id,
            group=None,
        )
        store.add_payload(control_message_id=message_id, payload=payload)

    assert not store.is_pending_prompt(chat_id=10, prompt_message_id=1)
    assert store.is_pending_prompt(chat_id=10, prompt_message_id=2)
    assert store.get_payload(control_message_id=1) is None
    assert store.get_payload(control_message_id=3) is payload


def test_telegram_album_buffers_are_bounded() -> None:
    broadcast = AiogramBroadcastSessionStore(max_pending_items=2)
    cash = CityCashMediaStore(max_groups=2)
    messages = [SimpleNamespace(message_id=index) for index in range(1, 4)]

    for index, message in enumerate(messages, start=1):
        broadcast.add_media_group_message(
            chat_id=10,
            media_group_id=str(index),
            message=message,  # type: ignore[arg-type]
        )
        cash.add_message(
            chat_id=10,
            media_group_id=str(index),
            message=message,  # type: ignore[arg-type]
        )

    assert not broadcast.has_media_group((10, "1"))
    assert broadcast.has_media_group((10, "3"))
    assert cash.group_size(chat_id=10, media_group_id="1") == 0
    assert cash.group_size(chat_id=10, media_group_id="3") == 1
