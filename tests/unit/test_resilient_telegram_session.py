import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import GetUpdates, SendMessage, SendPhoto

from telegram_adapters import resilient_session as module


@pytest.fixture
def transports(monkeypatch):
    created = []

    def create():
        item = SimpleNamespace(make_request=AsyncMock(return_value=[]), close=AsyncMock())
        created.append(item)
        return item

    monkeypatch.setattr(module, "AiohttpSession", create)
    return created


async def fail_poll(session, transport, clock, seconds):
    clock[0] = seconds
    method = GetUpdates(offset=123)
    transport.make_request.side_effect = TelegramNetworkError(method=method, message="timeout")
    with pytest.raises(TelegramNetworkError):
        await session.make_request(SimpleNamespace(), method, timeout=90)


async def test_poll_rotation_preserves_api_and_does_not_replay_requests(transports, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    session = module.ResilientTelegramSession()
    api = TelegramAPIServer(
        base="https://example.invalid/bot{token}/test/{method}",
        file="https://example.invalid/{path}",
    )
    session.api = api
    first = transports[0]
    for seconds in (0, 60, 121):
        await fail_poll(session, first, clock, seconds)
    assert len(transports) == 2
    assert first.make_request.await_count == 3
    first.close.assert_awaited_once()
    second = transports[1]
    await session.make_request(SimpleNamespace(), GetUpdates(offset=123), timeout=90)
    assert second.api is api
    assert second.make_request.call_args.kwargs["timeout"] == 90
    assert session._failures == 0
    await session.close()
    second.close.assert_awaited_once()
    assert session._transports == []


async def test_successful_poll_resets_failure_window(transports, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    session = module.ResilientTelegramSession()
    first = transports[0]
    await fail_poll(session, first, clock, 0)
    first.make_request.side_effect = None
    await session.make_request(SimpleNamespace(), GetUpdates())
    await fail_poll(session, first, clock, 200)
    assert len(transports) == 1
    assert session._failures == 1
    await session.close()


async def test_rotation_drains_existing_send_without_closing_it(transports, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    session = module.ResilientTelegramSession()
    first = transports[0]
    entered, finish = asyncio.Event(), asyncio.Event()

    async def request(bot, method, timeout=None):
        if isinstance(method, SendMessage):
            entered.set()
            await finish.wait()
            return "sent once"
        raise TelegramNetworkError(method=method, message="timeout")

    first.make_request.side_effect = request
    task = asyncio.create_task(
        session.make_request(SimpleNamespace(), SendMessage(chat_id=1, text="reply"))
    )
    await entered.wait()
    try:
        for seconds in (0, 60, 121):
            clock[0] = seconds
            with pytest.raises(TelegramNetworkError):
                await session.make_request(SimpleNamespace(), GetUpdates())
        assert len(transports) == 2
        first.close.assert_not_awaited()
        finish.set()
        assert await task == "sent once"
        first.close.assert_awaited_once()
    finally:
        finish.set()
        await task
        await session.close()


async def test_outbound_network_error_is_not_replayed_or_counted(transports):
    session = module.ResilientTelegramSession()
    method = SendMessage(chat_id=1, text="reply")
    transports[0].make_request.side_effect = TelegramNetworkError(method=method, message="timeout")
    with pytest.raises(TelegramNetworkError):
        await session.make_request(SimpleNamespace(), method)
    transports[0].make_request.assert_awaited_once()
    assert session._failures == 0
    await session.close()


async def test_recovery_is_bounded_when_old_requests_are_still_draining(transports, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    session = module.ResilientTelegramSession()
    held = []
    try:
        for base in (0, 250, 500):
            held.append(session._acquire())
            for seconds in (base, base + 60, base + 121):
                await fail_poll(session, session._current.session, clock, seconds)
        assert len(transports) == 3
    finally:
        for transport in held:
            await session._release(transport)
        await session.close()
    for transport in transports:
        transport.close.assert_awaited_once()


async def test_download_stream_releases_transport_when_closed_early(transports):
    session = module.ResilientTelegramSession()
    closed = []

    async def stream(*args, **kwargs):
        try:
            yield b"one"
            yield b"two"
        finally:
            closed.append(True)

    transports[0].stream_content = stream
    download = session.stream_content("https://example.invalid/file")
    assert await anext(download) == b"one"
    assert session._current.active == 1
    await download.aclose()
    assert closed == [True]
    assert session._current.active == 0
    await session.close()


async def test_retry_after_retries_only_rejected_response(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    method = SendMessage(chat_id=1, text="balance")
    send = AsyncMock(
        side_effect=[TelegramRetryAfter(method=method, message="flood", retry_after=3), "sent"]
    )
    assert await module.TelegramResponseRetryMiddleware()(send, SimpleNamespace(), method) == "sent"
    assert send.await_count == 2
    sleep.assert_awaited_once_with(3.1)


@pytest.mark.parametrize("kind", ["exhausted", "long", "network", "bad_request", "upload"])
async def test_retry_is_bounded_and_does_not_replay_uncertain_or_upload_requests(kind, monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    method = (
        SendPhoto(chat_id=1, photo="file_id")
        if kind == "upload"
        else SendMessage(chat_id=1, text="balance")
    )
    if kind == "network":
        error = TelegramNetworkError(method=method, message="timeout")
    elif kind == "bad_request":
        error = TelegramBadRequest(method=method, message="invalid")
    else:
        error = TelegramRetryAfter(
            method=method, message="flood", retry_after=60 if kind == "long" else 1
        )
    send = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        await module.TelegramResponseRetryMiddleware()(send, SimpleNamespace(), method)
    assert send.await_count == (3 if kind == "exhausted" else 1)


async def test_cancel_during_retry_does_not_send_again(monkeypatch):
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    method = SendMessage(chat_id=1, text="balance")
    send = AsyncMock(side_effect=TelegramRetryAfter(method=method, message="flood", retry_after=3))
    with pytest.raises(asyncio.CancelledError):
        await module.TelegramResponseRetryMiddleware()(send, SimpleNamespace(), method)
    send.assert_awaited_once()


async def test_bot_middleware_archives_success_once_and_keeps_handler_outside_retry(
    transports, monkeypatch
):
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    session = module.ResilientTelegramSession()
    session.middleware(module.TelegramResponseRetryMiddleware())
    archived = []

    async def archive(make_request, bot, method):
        result = await make_request(bot, method)
        archived.append(result)
        return result

    session.middleware(archive)
    bot = Bot(token="123456:TEST_TOKEN", session=session)
    method = SendMessage(chat_id=1, text="reply")
    transports[0].make_request.side_effect = [
        TelegramRetryAfter(method=method, message="flood", retry_after=3),
        "sent",
    ]
    business_action = AsyncMock()
    await business_action()
    assert await bot(method) == "sent"
    business_action.assert_awaited_once()
    assert archived == ["sent"]
    assert transports[0].make_request.await_count == 2
    await session.close()


async def test_retry_total_wait_budget(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    method = SendMessage(chat_id=1, text="reply")
    send = AsyncMock(side_effect=TelegramRetryAfter(method=method, message="flood", retry_after=25))
    with pytest.raises(TelegramRetryAfter):
        await module.TelegramResponseRetryMiddleware()(send, SimpleNamespace(), method)
    assert send.await_count == 2
    assert sleep.await_count == 1
