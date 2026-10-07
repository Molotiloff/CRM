from __future__ import annotations

import asyncio
import inspect
import json
import logging
import ssl
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import certifi
import websockets
from websockets.typing import Origin

from services.lifecycle import ManagedTaskLifecycle

log = logging.getLogger(__name__)
WS_URL = "wss://hub.abcex.io/websocket/exchange"
ORDERBOOK_STREAM = "USDTRUB@orderbook"
PING_INTERVAL = 10
RECV_TIMEOUT = 35
MAX_BOOK_LEVELS = 10000


class AbcexWsService(ManagedTaskLifecycle):
    """Public ABCEX USDT/RUB book: snapshot followed by additive volume deltas."""

    def __init__(
        self,
        *,
        on_orderbook_update: Callable[[], Awaitable[None] | None] | None = None,
    ) -> None:
        super().__init__(task_name="abcex_ws_service")
        self.on_orderbook_update = on_orderbook_update
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())
        self._asks: dict[Decimal, Decimal] = {}
        self._bids: dict[Decimal, Decimal] = {}
        self._sequence: int | None = None
        self.orderbook_observed_at: datetime | None = None

    @property
    def best_ask(self) -> Decimal | None:
        return min(self._asks) if self._asks else None

    def get_best_bid(self) -> Decimal | None:
        return max(self._bids) if self._bids else None

    def get_asks(self) -> list[dict]:
        return self._levels(self._asks, reverse=False)

    def get_bids(self) -> list[dict]:
        return self._levels(self._bids, reverse=True)

    @staticmethod
    def _levels(book: dict[Decimal, Decimal], *, reverse: bool) -> list[dict]:
        return [
            {"price": str(price), "volume": str(book[price])}
            for price in sorted(book, reverse=reverse)
        ]

    def _reset(self) -> None:
        self._asks.clear()
        self._bids.clear()
        self._sequence = None
        self.orderbook_observed_at = None

    @staticmethod
    def _apply(book: dict[Decimal, Decimal], rows: list, *, snapshot: bool) -> None:
        for price_raw, volume_raw in rows:
            price, change = Decimal(price_raw), Decimal(volume_raw)
            if not price.is_finite() or price <= 0 or not change.is_finite():
                raise ValueError("Invalid ABCEX orderbook level")
            volume = change if snapshot else book.get(price, Decimal(0)) + change
            if volume < 0:
                raise ValueError("Negative ABCEX orderbook volume; snapshot required")
            if volume == 0:
                book.pop(price, None)
            else:
                book[price] = volume

    async def _handle_message(self, raw: str | bytes) -> None:
        # The hub may delimit multiple JSON messages with newlines.
        for line in raw.splitlines():
            if not line.strip():
                continue
            message = json.loads(line)
            if message.get("stream") != ORDERBOOK_STREAM:
                continue
            data = message["data"]
            if data.get("marketId") != "USDTRUB":
                continue
            kind = data.get("type")
            if kind not in {"snapshot", "delta"}:
                continue
            sequence = int(data["seq"])
            snapshot = kind == "snapshot"
            if not snapshot:
                if self._sequence is None:
                    raise ValueError("ABCEX delta received before snapshot")
                if sequence <= self._sequence:
                    continue
                if sequence != self._sequence + 1:
                    raise ValueError("ABCEX sequence gap; snapshot required")
            asks = {} if snapshot else self._asks.copy()
            bids = {} if snapshot else self._bids.copy()
            self._apply(asks, data["asks"], snapshot=snapshot)
            self._apply(bids, data["bids"], snapshot=snapshot)
            if len(asks) > MAX_BOOK_LEVELS or len(bids) > MAX_BOOK_LEVELS:
                raise ValueError("ABCEX orderbook too large; snapshot required")
            self._asks, self._bids = asks, bids
            self._sequence = sequence
            self.orderbook_observed_at = datetime.now(UTC)
            if self.on_orderbook_update:
                try:
                    result = self.on_orderbook_update()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    log.exception("ABCEX orderbook callback failed")

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await ws.send(json.dumps({"method": "ping", "data": int(time.time() * 1000)}))

    async def _consume(self, ws) -> None:
        await ws.send(json.dumps({"method": "subscribe", "data": [ORDERBOOK_STREAM]}))
        while not self.is_stopping:
            raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
            await self._handle_message(raw)

    async def _run(self) -> None:
        while not self.is_stopping:
            try:
                self._reset()
                async with websockets.connect(
                    WS_URL,
                    origin=Origin("https://abcex.io"),
                    ssl=self._ssl_context,
                    ping_interval=None,
                    compression=None,
                    close_timeout=10,
                    open_timeout=15,
                    max_size=4 * 1024 * 1024,
                    max_queue=4,
                ) as ws:
                    log.info("ABCEX websocket connected")
                    async with asyncio.TaskGroup() as group:
                        heartbeat = group.create_task(self._heartbeat(ws))
                        try:
                            await self._consume(ws)
                        finally:
                            heartbeat.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — reconnect boundary for network/protocol errors
                log.warning("ABCEX websocket reconnecting: %s", exc)
            finally:
                self._reset()
            if not self.is_stopping:
                await asyncio.sleep(3)
