import asyncio
import base64
import json
import struct
import time
import zlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Update

from middlewares.widget_query import WidgetQueryMiddleware
from services.rate_order.candles import Candle, CandleStore
from services.rate_order.orderbook_service import OrderbookService
from services.rate_order.rapira_widget_payload import HTML_PATH, widget_state
from services.rate_order.rapira_widget_service import RapiraWidgetService
from services.rate_order.widget_binding import WidgetBinding
from services.rate_order.widget_errors import WidgetDeliveryError
from telegram_adapters.widget_api import AnswerWidgetQuery, EditMessageWidgetData, SendWidget

T = 1794475800 // 3600 * 3600


def row(t=T, volume="10"):
    return [t * 1000, "86.30", "86.40", "86.20", "86.35", volume]


def tick(t=T, volume="10"):
    return dict(
        zip(
            ("time", "openPrice", "highestPrice", "lowestPrice", "closePrice", "volume"),
            row(t, volume),
            strict=True,
        ),
        symbol="USDT_RUB",
        period="1min",
    )


def service():
    api = SimpleNamespace(
        send=AsyncMock(return_value=100),
        edit=AsyncMock(),
        caption=AsyncMock(),
        answer=AsyncMock(),
        text=AsyncMock(return_value=200),
    )
    repo = SimpleNamespace(load=AsyncMock(return_value=[]), save=AsyncMock(), remove=AsyncMock())
    ws = SimpleNamespace(
        get_asks=lambda: [{"price": "86.4", "volume": "500000"}],
        get_bids=lambda: [{"price": "86.3", "volume": "500000"}],
        orderbook_observed_at=datetime.now(UTC),
    )
    book = OrderbookService(
        ws_service=ws, repo=None, live_message_editor=None, exchange_name="Rapira"
    )
    obj = RapiraWidgetService(repository=repo, gateway=api, orderbook=book)
    obj._history = AsyncMock()
    obj.store.replace(15, [row()], observed_at=int(time.time()))
    return obj, repo, api


def test_minute_replacements_do_not_double_count_volume_and_store_is_bounded():
    store = CandleStore()
    store.replace(15, [row(T - 900)], observed_at=T)
    store.on_minute(tick(T, "10"))
    store.on_minute(tick(T, "12"))
    store.on_minute(tick(T + 60, "20"))
    store.on_minute(tick(T, "1"))  # delayed packet must not replace a newer one
    result = store.window(15)[-1]
    assert result.volume == 32
    assert result.time == T
    for index in range(500):
        store.on_minute(tick(T + index * 60))
    assert len(store.minutes) == 180
    store.replace(15, [row(T + index * 900) for index in range(1000)], observed_at=T)
    assert len(store.history[15]) == 256


def test_partial_minute_bucket_preserves_authoritative_history():
    store = CandleStore()
    store.replace(60, [row(T, "100")], observed_at=T)
    store.on_minute(tick(T + 120, "10"))
    assert store.window(60)[-1].volume == 100


@pytest.mark.parametrize(
    "invalid", [[], row(volume="NaN"), row(volume="-1"), [T * 1000, 3, 1, 2, 4, 5]]
)
def test_malformed_candles_are_rejected(invalid):
    with pytest.raises(ValueError):
        Candle.parse(invalid)


def test_template_and_compact_payload_meet_real_telegram_limits():
    html = HTML_PATH.read_bytes()
    assert len(html) <= 32768
    assert len(zlib.compress(html, 9)) <= 4096
    assert b"<iframe" not in html and b"<script src=" not in html
    rows = [Candle.parse(row(T + index * 900)) for index in range(500)]
    raw = widget_state(rows, frame=15, revision=1794475800000, history_at=T, book_at=T)
    assert len(raw.encode()) <= 1024
    data = json.loads(raw)
    decoded = list(struct.iter_unpack("<I4Hf", base64.b64decode(data["d"])))
    assert 25 <= len(decoded) <= 48
    last = decoded[-1]
    assert last[0] == rows[-1].time
    assert [(value + data["p"]) / 100 for value in last[1:5]] == [86.3, 86.4, 86.2, 86.35]


async def test_widget_send_uses_real_html_and_persists_binding():
    obj, repo, api = service()
    assert await obj.send(42) == 100
    args = api.send.call_args.kwargs
    assert "<canvas" in args["html"]
    assert "Глубина стакана продаж" in args["caption"]
    assert "Глубина стакана покупок" in args["caption"]
    assert len(args["caption"].encode("utf-16-le")) // 2 <= 1024
    assert obj.bindings[42].message_id == 100
    repo.save.assert_awaited_once()


async def test_query_authorization_identity_and_timeframe_are_checked():
    obj, repo, api = service()
    obj.bindings[42] = WidgetBinding(42, 100)
    base = {"query_id": "q", "user_id": 42, "chat_id": 42, "message_id": 100, "authorized": True}
    await obj.query(**{**base, "authorized": False}, data="tf:5")
    await obj.query(**{**base, "message_id": 99}, data="tf:5")
    await obj.query(**base, data="tf:999")
    repo.save.assert_not_awaited()
    assert obj.bindings[42].frame == 15
    await obj.query(**base, data="tf:5")
    assert obj.bindings[42].frame == 5
    assert obj._wake.is_set()
    repo.save.assert_awaited_once()


async def test_retry_after_blocks_next_publish_without_a_retry_loop():
    obj, repo, api = service()
    obj.bindings[42] = WidgetBinding(42, 100)
    api.edit.side_effect = WidgetDeliveryError("retry", retry_after=60)
    await obj.refresh()
    await obj.refresh()
    api.edit.assert_awaited_once()
    assert obj._blocked_until > time.monotonic()
    assert 42 in obj.bindings
    repo.remove.assert_not_awaited()


async def test_deleted_widget_is_removed_without_affecting_other_messages():
    obj, repo, api = service()
    obj.bindings[42] = WidgetBinding(42, 100)
    api.edit.side_effect = WidgetDeliveryError("missing")
    await obj.refresh()
    assert not obj.bindings
    repo.remove.assert_awaited_once_with(42)


async def test_long_caption_keeps_all_orderbook_text():
    obj, repo, api = service()
    text = "Источник 🟡 0.01%\n" * 500
    obj.orderbook.build_live_text = lambda: text
    await obj.send(42)
    chunks = [call.kwargs["text"] for call in api.text.call_args_list]
    assert text in "".join(chunks)
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 4096 for chunk in chunks)
    assert len(obj.bindings[42].tails) == len(chunks)


async def test_raw_widget_update_is_handled_before_aiogram_unknown_update_dispatch():
    obj, _, api = service()
    obj.bindings[42] = WidgetBinding(42, 100)
    managers = SimpleNamespace(is_manager=AsyncMock(return_value=True))
    middleware = WidgetQueryMiddleware(obj, managers, admin_ids=[], admin_chat_ids=[])
    fallback = AsyncMock()
    event = Update.model_validate(
        {
            "update_id": 1,
            "widget_query": {
                "id": "q",
                "from": {"id": 42, "is_bot": False, "first_name": "Manager"},
                "chat": {"id": 42, "type": "private"},
                "message_id": 100,
                "data": "tf:60",
                "type": {"type": "data"},
                "chat_instance": "test",
            },
        }
    )
    assert await middleware(fallback, event, {}) is True
    fallback.assert_not_awaited()
    assert obj.bindings[42].frame == 60
    api.answer.assert_awaited_once()


async def test_publisher_closes_http_client_on_shutdown(monkeypatch):
    obj, repo, _ = service()
    ready = asyncio.Event()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[row()]))
    )
    monkeypatch.setattr(
        "services.rate_order.rapira_widget_service.httpx.AsyncClient", lambda **kwargs: client
    )

    async def loaded(*, limit):
        ready.set()
        return [WidgetBinding(42, 100)]

    repo.load.side_effect = loaded
    await obj.start()
    await ready.wait()
    await asyncio.sleep(0)
    await obj.stop()
    assert client.is_closed
    assert not obj.bindings
    assert obj._client is None


def test_dispatcher_keeps_extra_widget_query_fields():
    # Compatibility is supported by TelegramObject's extra='allow', not a
    # monkey-patched aiogram Update or a second getUpdates consumer.
    assert Dispatcher().update.outer_middleware is not None
    event = Update.model_validate({"update_id": 1, "widget_query": {"id": "q"}})
    assert event.widget_query == {"id": "q"}


async def test_widgets_use_full_dispatcher_pipeline_without_a_second_poller():
    obj, _, api = service()
    obj.bindings[42] = WidgetBinding(42, 100)
    dp = Dispatcher()
    dp.update.outer_middleware(
        WidgetQueryMiddleware(
            obj,
            SimpleNamespace(is_manager=AsyncMock(return_value=True)),
            admin_ids=[],
            admin_chat_ids=[],
        )
    )
    bot = Bot("123456:TEST")
    try:
        result = await dp.feed_raw_update(
            bot,
            {
                "update_id": 10,
                "widget_query": {
                    "id": "q",
                    "from": {"id": 42, "is_bot": False, "first_name": "Test"},
                    "chat": {"id": 42, "type": "private"},
                    "message_id": 100,
                    "chat_instance": "test",
                    "data": "tf:5",
                    "type": {"type": "data"},
                },
            },
        )
        assert result is True
        assert obj.bindings[42].frame == 5
        api.answer.assert_awaited_once()
    finally:
        await bot.session.close()
        await dp.storage.close()


def test_bot_api_104_method_models_keep_expected_names_and_fields():
    sent = SendWidget(chat_id=42, html="<b>chart</b>", chat_data="{}", caption="Стаканы")
    assert sent.__api_method__ == "sendWidget"
    assert sent.__returning__.__name__ == "Message"
    assert sent.model_dump()["allow_queries"] is True
    assert sent.model_dump()["parse_mode"] is None
    edited = EditMessageWidgetData(chat_id=42, message_id=100, chat_data="{}")
    assert edited.__api_method__ == "editMessageWidgetData"
    assert AnswerWidgetQuery(widget_query_id="q").__api_method__ == "answerWidgetQuery"


async def test_history_reads_bounded_public_data_without_auth_and_respects_cache():
    obj, _, _ = service()
    del obj._history
    requests = []

    def response(request):
        requests.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert request.url.params["symbol"] == "USDT/RUB"
        return httpx.Response(200, json=[row()])

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        obj._client = client
        await obj._history()
        await obj._history()
    assert len(requests) == 4
    assert set(obj.store.observed_at) == {1, 5, 15, 60}


async def test_history_rejects_oversized_response_and_closes_stream():
    obj, _, _ = service()
    del obj._history
    before = dict(obj.store.observed_at)
    responses = []

    def oversized(request):
        response = httpx.Response(200, content=b" " * 262145)
        responses.append(response)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(oversized)) as client:
        obj._client = client
        await obj._history()
    assert obj.store.observed_at == before
    assert all(response.is_closed for response in responses)


async def test_production_rejects_group_widget_and_capacity_is_bounded():
    obj, _, api = service()
    with pytest.raises(RuntimeError, match="личке"):
        await obj.send(-100)
    for index in range(obj.LIMIT):
        obj.bindings[index + 1] = WidgetBinding(index + 1, 100)
    with pytest.raises(RuntimeError, match="лимит"):
        await obj.send(99999)
    api.send.assert_not_awaited()
