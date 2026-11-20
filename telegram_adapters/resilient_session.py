"""Recover polling transport without replaying uncertain outbound requests."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import GetUpdates, TelegramMethod
from aiogram.methods.base import TelegramType

log = logging.getLogger(__name__)


@dataclass(eq=False)
class _Transport:
    session: AiohttpSession
    active: int = 0
    retired: bool = False


class ResilientTelegramSession(BaseSession):
    FAILURE_THRESHOLD = 3
    FAILURE_SECONDS = 120.0
    RESET_COOLDOWN = 120.0

    def __init__(self) -> None:
        super().__init__()
        self._current = _Transport(AiohttpSession())
        self._transports = [self._current]
        self._closed = False
        self._failures = 0
        self._failed_since: float | None = None
        self._last_reset = float("-inf")
        self._awaiting_recovery = False

    def _acquire(self) -> _Transport:
        if self._closed:
            raise RuntimeError("Telegram session is closed")
        transport = self._current
        transport.active += 1
        transport.session.api = self.api
        transport.session.timeout = self.timeout
        transport.session.json_loads = self.json_loads
        transport.session.json_dumps = self.json_dumps
        return transport

    async def _release(self, transport: _Transport) -> None:
        transport.active -= 1
        if transport.retired and transport.active == 0 and transport in self._transports:
            # Remove before awaiting: no second closer can claim this transport.
            self._transports.remove(transport)
            await transport.session.close()

    def _poll_failed(self, transport: _Transport) -> None:
        if transport is not self._current or self._closed:
            return
        now = time.monotonic()
        self._failures += 1
        if self._failed_since is None:
            self._failed_since = now
        if (
            self._failures < self.FAILURE_THRESHOLD
            or now - self._failed_since < self.FAILURE_SECONDS
            or now - self._last_reset < self.RESET_COOLDOWN
            or len(self._transports) >= 3
        ):
            return
        log.warning(
            "Recovering Telegram HTTP transport after polling network failures: failures=%s downtime_seconds=%.1f active_requests=%s",
            self._failures,
            now - self._failed_since,
            transport.active,
        )
        transport.retired = True
        self._current = _Transport(AiohttpSession())
        self._transports.append(self._current)
        self._last_reset = now
        self._awaiting_recovery = True
        self._failures = 0
        self._failed_since = None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        transport = self._acquire()
        try:
            result = await transport.session.make_request(bot, method, timeout=timeout)
            if isinstance(method, GetUpdates) and transport is self._current:
                if self._failures or self._awaiting_recovery:
                    log.info(
                        "Telegram polling recovered: recent_network_failures=%s transport_rotated=%s",
                        self._failures,
                        self._awaiting_recovery,
                    )
                self._failures = 0
                self._failed_since = None
                self._awaiting_recovery = False
            return result
        except TelegramNetworkError:
            if isinstance(method, GetUpdates):
                self._poll_failed(transport)
            raise
        finally:
            await self._release(transport)

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        transport = self._acquire()
        stream = transport.session.stream_content(
            url,
            headers=headers,
            timeout=timeout,
            chunk_size=chunk_size,
            raise_for_status=raise_for_status,
        )
        try:
            async for chunk in stream:
                yield chunk
        finally:
            try:
                await stream.aclose()
            finally:
                await self._release(transport)

    async def close(self) -> None:
        self._closed = True
        transports, self._transports = self._transports, []
        await asyncio.gather(*(item.session.close() for item in transports))


class TelegramResponseRetryMiddleware:
    """Retry only explicit flood rejection, never the handler/business action.

    Uploads and background widget updates retain their existing recovery policy.
    No retry for timeouts/5xx: the server might already have sent the message.
    """

    METHODS = frozenset(
        {"sendMessage", "editMessageText", "editMessageCaption", "editMessageReplyMarkup"}
    )
    MAX_RETRIES = 2
    MAX_WAIT = 30.0
    MAX_TOTAL_WAIT = 45.0

    async def __call__(self, make_request: Any, bot: Bot, method: TelegramMethod[Any]) -> Any:
        waited = 0.0
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                delay = max(0.0, float(exc.retry_after)) + 0.1
                if (
                    method.__api_method__ not in self.METHODS
                    or attempt >= self.MAX_RETRIES
                    or delay > self.MAX_WAIT
                    or waited + delay > self.MAX_TOTAL_WAIT
                ):
                    raise
                log.warning(
                    "Retrying Telegram response after flood rejection: method=%s chat_id=%s attempt=%s delay_seconds=%.1f",
                    method.__api_method__,
                    getattr(method, "chat_id", None),
                    attempt + 1,
                    delay,
                )
                await asyncio.sleep(delay)
                waited += delay
