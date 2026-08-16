from __future__ import annotations

import asyncio
import inspect
import json
import logging
import ssl
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation

import certifi
import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from services.lifecycle import ManagedTaskLifecycle

log = logging.getLogger("rapira_ws")

WS_URL = "wss://api.rapira.net/market-ws/?EIO=4&transport=websocket"

# engine.io v4 сервер сам шлёт heartbeat-ping ("2") каждые ~25 c. Если за это
# время не пришло НИ heartbeat'а, НИ данных — соединение мёртвое, реконнектимся.
# Запас (40 c) больше серверного pingInterval, чтобы не рвать живой коннект.
RECV_TIMEOUT = 40

SUBSCRIBE_PAYLOAD = [
    "subscribe",
    [
        "spot@public.depth@USDT_RUB",
        "spot@public.deals@USDT_RUB",
        "spot@public.ticker@USDT_RUB",
        "spot@public.kline@USDT_RUB",
    ],
]


class RapiraWsService(ManagedTaskLifecycle):
    """
    Совместим по интерфейсу с прежним GrinexWsService:
      - start / stop
      - get_asks / get_bids
      - best_ask
      - get_best_bid
      - on_best_ask
      - on_orderbook_update

    Это позволит оставить /гар, /гар- и /гарред как есть,
    просто подменив источник данных на Rapira.
    """

    def __init__(
        self,
        *,
        on_best_ask: Callable[[Decimal], Awaitable[None] | None] | None = None,
        on_orderbook_update: Callable[[], Awaitable[None] | None] | None = None,
    ) -> None:
        super().__init__(task_name="rapira_ws_service")
        self.on_best_ask = on_best_ask
        self.on_orderbook_update = on_orderbook_update

        self.best_ask: Decimal | None = None
        self.asks: list[dict] = []
        self.bids: list[dict] = []

        self._ssl_context = ssl.create_default_context(cafile=certifi.where())

    def get_asks(self) -> list[dict]:
        return list(self.asks)

    def get_bids(self) -> list[dict]:
        return list(self.bids)

    def get_best_bid(self) -> Decimal | None:
        if not self.bids:
            return None
        try:
            return Decimal(str(self.bids[0]["price"]))
        except (KeyError, IndexError, TypeError, InvalidOperation):
            return None

    async def _notify_best_ask(self, best_ask: Decimal) -> None:
        cb = self.on_best_ask
        if cb is None:
            return

        try:
            result = cb(best_ask)
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.exception("Rapira on_best_ask callback error")

    async def _notify_orderbook_update(self) -> None:
        cb = self.on_orderbook_update
        if cb is None:
            return

        try:
            result = cb()
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.exception("Rapira on_orderbook_update callback error")

    @staticmethod
    def _normalize_items(items: list[dict], *, reverse_price: bool) -> list[dict]:
        normalized: list[dict] = []

        for item in items:
            try:
                price = Decimal(str(item["price"]))
                amount = Decimal(str(item["amount"]))
            except (KeyError, TypeError, InvalidOperation):
                continue

            normalized.append(
                {
                    "price": str(price),
                    "volume": str(amount),
                }
            )

        normalized.sort(
            key=lambda x: Decimal(str(x["price"])),
            reverse=reverse_price,
        )
        return normalized

    async def _handle_depth_payload(self, payload: dict) -> None:
        symbol = str(payload.get("symbol") or "")
        if symbol != "USDT/RUB":
            return

        direction = str(payload.get("direction") or "").upper()
        items = payload.get("items") or []
        if not isinstance(items, list):
            return

        normalized = self._normalize_items(
            items,
            reverse_price=(direction == "BUY"),
        )

        if direction == "SELL":
            self.asks = normalized
        elif direction == "BUY":
            self.bids = normalized
        else:
            return

        prev_best_ask = self.best_ask

        try:
            self.best_ask = (
                Decimal(str(self.asks[0]["price"]))
                if self.asks else None
            )
        except (KeyError, IndexError, TypeError, InvalidOperation):
            self.best_ask = None

        await self._notify_orderbook_update()

        if self.best_ask is not None and self.best_ask != prev_best_ask:
            await self._notify_best_ask(self.best_ask)

    async def _handle_socketio_message(self, raw: str, ws) -> None:
        # engine.io ping -> pong
        if raw == "2":
            await ws.send("3")
            return

        # ignore pong / connect acks / service frames
        if raw in {"3", "40"}:
            return

        # socket.io event
        if not raw.startswith("42"):
            return

        try:
            data = json.loads(raw[2:])
        except (json.JSONDecodeError, TypeError):
            return

        if not isinstance(data, list) or len(data) < 2:
            return

        event_name = data[0]
        payload = data[1]

        if event_name == "depth" and isinstance(payload, dict):
            await self._handle_depth_payload(payload)

    async def _subscribe(self, ws) -> None:
        payload = "42" + json.dumps(SUBSCRIBE_PAYLOAD, ensure_ascii=False, separators=(",", ":"))
        await ws.send(payload)
        log.info("Rapira subscribed to USDT_RUB channels")

    async def _run(self) -> None:
        reconnect_delay = 3

        while not self.is_stopping:
            try:
                log.info("Rapira websocket connecting")

                async with websockets.connect(
                    WS_URL,
                    origin="https://rapira.net",
                    ssl=self._ssl_context,
                    # Протокольный WS-ping ОТКЛЮЧЁН: socket.io/engine.io-сервер не
                    # отвечает на raw ping-фреймы, поэтому websockets через
                    # ping_timeout рвал живое соединение каждые ~50 c (close 1011
                    # 'keepalive ping timeout'). Живость держим через engine.io
                    # heartbeat ("2"/"3") + read-timeout на recv (см. RECV_TIMEOUT).
                    ping_interval=None,
                    close_timeout=10,
                    max_size=4 * 1024 * 1024,
                ) as ws:
                    log.info("Rapira websocket connected")

                    # 1) ждём engine.io open packet: 0{...}
                    first = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
                    if not isinstance(first, str):
                        first = first.decode("utf-8", errors="ignore")

                    if not str(first).startswith("0"):
                        log.warning("Unexpected Rapira handshake packet: %r", first)

                    # 2) socket.io connect
                    await ws.send("40")

                    # 3) читаем, пока не увидим 40 / 40{...}, потом подписываемся
                    subscribed = False

                    while not self.is_stopping:
                        raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
                        if not isinstance(raw, str):
                            raw = raw.decode("utf-8", errors="ignore")

                        if raw == "2":
                            await ws.send("3")
                            continue

                        if raw == "40" or raw.startswith("40{"):
                            if not subscribed:
                                await self._subscribe(ws)
                                subscribed = True
                            continue

                        if not subscribed:
                            # некоторые сервера шлют данные почти сразу
                            # но без подписки не работаем
                            continue

                        await self._handle_socketio_message(raw, ws)

            except asyncio.CancelledError:
                raise

            except TimeoutError:
                # За RECV_TIMEOUT не пришло ни heartbeat'а, ни данных — соединение
                # «молчит». Это штатная детекция мёртвого коннекта, не ошибка.
                log.info("Rapira websocket idle for %ss, reconnecting", RECV_TIMEOUT)
                if not self.is_stopping:
                    await asyncio.sleep(reconnect_delay)

            except ConnectionClosedOK:
                log.info("Rapira websocket closed normally")
                if not self.is_stopping:
                    log.info("Rapira websocket reconnecting in %s sec", reconnect_delay)
                    await asyncio.sleep(reconnect_delay)

            except ConnectionClosedError as e:
                # Реконнект штатный и занимает ~3 секунды — это не повод для WARNING.
                log.info("Rapira websocket connection lost: %r", e)
                if not self.is_stopping:
                    log.info("Rapira websocket reconnecting in %s sec", reconnect_delay)
                    await asyncio.sleep(reconnect_delay)

            except Exception:
                log.exception("Rapira websocket error")
                if not self.is_stopping:
                    log.info("Rapira websocket reconnecting in %s sec", reconnect_delay)
                    await asyncio.sleep(reconnect_delay)
