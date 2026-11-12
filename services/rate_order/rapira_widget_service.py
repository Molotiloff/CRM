from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from services.lifecycle import ManagedTaskLifecycle

from .candles import FRAMES, CandleStore
from .orderbook_service import OrderbookService
from .rapira_widget_payload import HTML_PATH, widget_state
from .widget_binding import WidgetBinding, WidgetBindingPort
from .widget_errors import WidgetDeliveryError

log = logging.getLogger(__name__)


class WidgetGatewayPort(Protocol):
    async def send(self, *, chat_id: int, html: str, state: str, caption: str) -> int: ...
    async def edit(self, *, chat_id: int, message_id: int, state: str) -> None: ...
    async def caption(self, *, chat_id: int, message_id: int, text: str): ...
    async def answer(self, query_id: str, data: str | None = None) -> None: ...
    async def text(self, *, chat_id: int, message_id: int | None, text: str) -> int: ...


class RapiraWidgetService(ManagedTaskLifecycle):
    LIMIT = 32
    INTERVAL = 10.0
    HISTORY_INTERVAL = 30.0

    def __init__(
        self,
        *,
        repository: WidgetBindingPort,
        gateway: WidgetGatewayPort,
        orderbook: OrderbookService,
        allow_groups: bool = False,
    ) -> None:
        super().__init__(task_name="rapira_widget_publisher")
        self.repo, self.api, self.orderbook = repository, gateway, orderbook
        self.store = CandleStore()
        self.allow_groups = allow_groups
        self.bindings: dict[int, WidgetBinding] = {}
        self._lock = asyncio.Lock()
        self._history_lock = asyncio.Lock()
        self._rest_due = 0.0
        self._blocked_until = 0.0
        self._last_push = 0.0
        self._revision = time.time_ns() // 1_000_000
        self._client: httpx.AsyncClient | None = None
        self._wake = asyncio.Event()

    async def _run(self) -> None:
        restored = await self.repo.load(limit=self.LIMIT)
        self.bindings = {
            item.chat_id: item for item in restored if item.chat_id > 0 or self.allow_groups
        }
        async with httpx.AsyncClient(
            timeout=8,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            follow_redirects=False,
        ) as client:
            self._client = client
            try:
                while True:
                    try:
                        await self.refresh()
                    except Exception:
                        log.exception("Rapira widget refresh failed")
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=self.INTERVAL)
                    self._wake.clear()
            finally:
                self._client = None

    async def _after_stop(self) -> None:
        self.bindings.clear()
        self.store = CandleStore()
        self._wake.clear()
        self._rest_due = self._blocked_until = self._last_push = 0.0

    def on_kline(self, payload: dict) -> None:
        try:
            self.store.on_minute(payload)
        except (ValueError, TypeError):
            log.warning("Invalid Rapira kline ignored")

    async def _history(self) -> None:
        if self._client is None:
            raise RuntimeError("Виджет ещё запускается; повторите команду через несколько секунд")
        async with self._history_lock:
            if time.monotonic() < self._rest_due:
                return
            self._rest_due = time.monotonic() + self.HISTORY_INTERVAL
            now = int(time.time())
            for frame in (1, *FRAMES):
                try:
                    async with self._client.stream(
                        "GET",
                        "https://api.rapira.net/market/history",
                        params={
                            "symbol": "USDT/RUB",
                            "from": (now - frame * 60 * 256) * 1000,
                            "to": now * 1000,
                            "resolution": str(frame),
                            "barsMaxSize": 256,
                        },
                    ) as response:
                        response.raise_for_status()
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(raw) + len(chunk) > 262144:
                                raise ValueError("Candle history response too large")
                            raw.extend(chunk)
                        rows = json.loads(raw, parse_float=Decimal)
                    if not isinstance(rows, list) or len(rows) > 1000:
                        raise ValueError("Invalid candle history response")
                    self.store.replace(frame, rows, observed_at=now)
                except (httpx.HTTPError, ValueError, TypeError):
                    log.warning("Rapira candle history unavailable for interval=%s", frame)

    def _state(self, frame: int) -> str:
        self._revision += 1
        observed = getattr(self.orderbook.ws_service, "orderbook_observed_at", None)
        book_at = int(observed.timestamp()) if isinstance(observed, datetime) else 0
        return widget_state(
            self.store.window(frame),
            frame=frame,
            revision=self._revision,
            history_at=self.store.observed_at.get(frame, 0),
            book_at=book_at,
        )

    def _caption(self) -> tuple[str, list[str]]:
        book = self.orderbook.build_live_text().rsplit("\n\nАктуально на ", 1)[0]
        observed = getattr(self.orderbook.ws_service, "orderbook_observed_at", None)
        stamp = (
            observed.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S МСК")
            if isinstance(observed, datetime)
            else "нет свежих данных"
        )
        book += f"\n\nДанные стакана на {stamp}"
        if isinstance(observed, datetime) and time.time() - observed.timestamp() > 45:
            book += "\n⚠ Данные стакана устарели"
        if len(book.encode("utf-16-le")) // 2 <= 1024:
            return book, []
        # Transport-independent splitter; keep all levels if the
        # caption is too short. Importing a Telegram adapter here is forbidden.
        from services.text_chunks import split_telegram_text

        chunks = list(split_telegram_text(book))
        if len(chunks) > 16:
            raise ValueError("Стакан слишком велик для публикации")
        return "〽️ Rapira · USDT/RUB\nПолные стаканы — в сообщениях ниже.", chunks

    async def _texts(self, binding: WidgetBinding, chunks: list[str]) -> None:
        for index in range(max(len(chunks), len(binding.tails))):
            old_id = binding.tails[index] if index < len(binding.tails) else None
            text = chunks[index] if index < len(chunks) else "Стаканы теперь в подписи к виджету."
            try:
                message_id = await self.api.text(
                    chat_id=binding.chat_id, message_id=old_id, text=text
                )
            except WidgetDeliveryError as exc:
                if exc.kind != "missing":
                    raise
                message_id = await self.api.text(
                    chat_id=binding.chat_id, message_id=None, text=text
                )
                binding.tails[index] = message_id
                await self.repo.save(binding)
            if old_id is None:
                binding.tails.append(message_id)
                await self.repo.save(binding)

    async def send(self, chat_id: int) -> int:
        if chat_id < 0 and not self.allow_groups:
            raise RuntimeError("В production виджет доступен только в личке с ботом")
        async with self._lock:
            if chat_id not in self.bindings and len(self.bindings) >= self.LIMIT:
                raise RuntimeError("Достигнут лимит активных виджетов")
            await self._history()
            caption, chunks = self._caption()
            message_id = await self.api.send(
                chat_id=chat_id, html=HTML_PATH.read_text(), state=self._state(15), caption=caption
            )
            binding = WidgetBinding(chat_id, message_id)
            self.bindings[chat_id] = binding
            await self.repo.save(binding)
            await self._texts(binding, chunks)
            return message_id

    async def query(
        self,
        *,
        query_id: str,
        user_id: int,
        chat_id: int,
        message_id: int,
        data: str,
        authorized: bool,
    ) -> None:
        del user_id
        binding = self.bindings.get(chat_id)
        if not authorized or binding is None or binding.message_id != message_id:
            await self.api.answer(
                query_id, json.dumps({"error": "Виджет недоступен или заменён"}, ensure_ascii=False)
            )
            return
        frame = {"tf:5": 5, "tf:15": 15, "tf:60": 60}.get(data)
        if data != "refresh" and frame is None:
            await self.api.answer(query_id, '{"error":"Unknown action"}')
            return
        # Acknowledge within 15s; updates run on the existing publisher, never
        # inside detached tasks and never behind a slow REST call in this query.
        await self.api.answer(query_id)
        async with self._lock:
            if self.bindings.get(chat_id) is not binding:
                return
            binding.frame = frame or binding.frame
            await self.repo.save(binding)
            self._wake.set()

    async def refresh(self) -> None:
        if not self.bindings or time.monotonic() < self._blocked_until:
            return
        await self._history()
        async with self._lock:
            delay = self._last_push + 5 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            caption, chunks = self._caption()
            for binding in list(self.bindings.values()):
                try:
                    await self.api.edit(
                        chat_id=binding.chat_id,
                        message_id=binding.message_id,
                        state=self._state(binding.frame),
                    )
                    await self.api.caption(
                        chat_id=binding.chat_id, message_id=binding.message_id, text=caption
                    )
                    await self._texts(binding, chunks)
                    await asyncio.sleep(0.1)
                except WidgetDeliveryError as exc:
                    if exc.kind == "retry":
                        self._blocked_until = time.monotonic() + max(1, exc.retry_after)
                        break
                    if exc.kind in {"missing", "forbidden"}:
                        await self.repo.remove(binding.chat_id)
                        self.bindings.pop(binding.chat_id, None)
                    else:
                        log.warning(
                            "Widget update failed chat_id=%s: %s",
                            binding.chat_id,
                            type(exc).__name__,
                        )
            self._last_push = time.monotonic()
