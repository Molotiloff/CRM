from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from db_asyncpg.ports.market import LiveMessageRepositoryPort
from services.rate_order.live_message_port import LiveMessageEditorPort
from services.rate_order.models import LiveMessageEditStatus

log = logging.getLogger("orderbook_service")


class OrderbookService:
    LIVE_MESSAGE_KEY = "orderbook_live"
    # Минимальный интервал между правками «живого» сообщения со стаканом.
    # Обновления стакана приходят по WS много раз в секунду; частые edit'ы
    # одного сообщения Telegram наказывает флуд-контролем (RetryAfter до
    # десятков секунд). Коалесцируем апдейты в одну правку раз в N секунд.
    MIN_REFRESH_INTERVAL_SECONDS = 5.0

    def __init__(
        self,
        *,
        ws_service,
        repo: LiveMessageRepositoryPort,
        live_message_editor: LiveMessageEditorPort,
        exchange_name: str = "Биржа",
        symbol_label: str = "USDT/RUB",
        disabled_reason: str | None = None,
    ) -> None:
        self.ws_service = ws_service
        self.repo = repo
        self.live_message_editor = live_message_editor
        self.exchange_name = exchange_name
        self.symbol_label = symbol_label
        self.disabled_reason = disabled_reason

        self._live_chat_id: int | None = None
        self._live_message_id: int | None = None
        self._refresh_lock = asyncio.Lock()
        self._pending_refresh = False
        self._retry_after_until = 0.0
        self._last_edit_at = 0.0

    @staticmethod
    def _fmt_num(v: Decimal) -> str:
        return f"{v:,.2f}"

    @staticmethod
    def _fmt_updated_at() -> str:
        return datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    def _title_asks(self) -> str:
        return f"〽️ Глубина стакана продаж для {self.symbol_label}"

    def _title_bids(self) -> str:
        return f"〽️ Глубина стакана покупок для {self.symbol_label}"

    def _title_bid(self) -> str:
        return f"〽️ Первый ордер на покупку для {self.symbol_label}"

    async def set_live_message(self, *, chat_id: int, message_id: int) -> None:
        self._live_chat_id = int(chat_id)
        self._live_message_id = int(message_id)

        await self.repo.upsert_live_message(
            chat_id=self._live_chat_id,
            message_key=self.LIVE_MESSAGE_KEY,
            message_id=self._live_message_id,
        )

    async def clear_live_message(self) -> None:
        if self._live_chat_id:
            await self.repo.delete_live_message(
                chat_id=self._live_chat_id,
                message_key=self.LIVE_MESSAGE_KEY,
            )

        self._live_chat_id = None
        self._live_message_id = None

    async def restore_live_message(self, *, admin_chat_id: int | None) -> None:
        if not admin_chat_id:
            return

        row = await self.repo.get_live_message(
            chat_id=int(admin_chat_id),
            message_key=self.LIVE_MESSAGE_KEY,
        )

        if not row:
            return

        self._live_chat_id = int(row["chat_id"])
        self._live_message_id = int(row["message_id"])

        log.info(
            "Restored live message: chat_id=%s message_id=%s",
            self._live_chat_id,
            self._live_message_id,
        )

    def _is_ws_available(self) -> bool:
        return self.ws_service is not None

    def _disabled_text(self) -> str:
        reason = self.disabled_reason or f"Подключение к {self.exchange_name} отключено."
        return reason

    def build_asks_depth_text(
        self,
        *,
        min_total_volume: Decimal = Decimal("500000"),
        min_order_volume: Decimal = Decimal("1000"),
    ) -> str:
        if not self._is_ws_available():
            return f"{self._title_asks()}\n\n{self._disabled_text()}"

        try:
            asks = self.ws_service.get_asks()
        except Exception as e:  # noqa: BLE001 — защитная граница к внешнему ws_service
            log.warning("Failed to get asks from ws_service: %r", e)
            return f"{self._title_asks()}\n\nСтакан {self.exchange_name} пока недоступен."

        if not asks:
            return f"{self._title_asks()}\n\nСтакан {self.exchange_name} пока недоступен."

        rows: list[tuple[Decimal, Decimal]] = []
        total_volume = Decimal("0")

        for item in asks:
            try:
                price = Decimal(str(item["price"]))
                volume = Decimal(str(item["volume"]))
            except (KeyError, TypeError, InvalidOperation):
                continue

            if volume < min_order_volume:
                continue

            rows.append((price, volume))
            total_volume += volume

            if total_volume >= min_total_volume:
                break

        if not rows:
            return f"{self._title_asks()}\n\nНет значимых ордеров (все < 1000)."

        price_width = max(len("Цена"), *(len(self._fmt_num(p)) for p, _ in rows))
        volume_width = max(len("Объём"), *(len(self._fmt_num(v)) for _, v in rows))

        header = f"{'Цена'.ljust(price_width)} | {'Объём'.rjust(volume_width)}"
        sep = "-" * (price_width + 3 + volume_width)

        lines = [
            self._title_asks(),
            header,
            sep,
        ]

        for price, volume in rows:
            lines.append(
                f"{self._fmt_num(price).rjust(price_width)} | {self._fmt_num(volume).rjust(volume_width)}"
            )

        lines += [
            sep,
            f"Всего объём: {self._fmt_num(total_volume)}",
            f"Количество ордеров: {len(rows)}",
        ]

        return "\n".join(lines)

    def build_bids_depth_text(
        self,
        *,
        min_total_volume: Decimal = Decimal("500000"),
        min_order_volume: Decimal = Decimal("1000"),
    ) -> str:
        if not self._is_ws_available():
            return f"{self._title_bids()}\n\n{self._disabled_text()}"

        try:
            bids = self.ws_service.get_bids()
        except Exception as e:  # noqa: BLE001 — защитная граница к внешнему ws_service
            log.warning("Failed to get bids from ws_service: %r", e)
            return f"{self._title_bids()}\n\nСтакан {self.exchange_name} пока недоступен."

        if not bids:
            return f"{self._title_bids()}\n\nСтакан {self.exchange_name} пока недоступен."

        rows: list[tuple[Decimal, Decimal]] = []
        total_volume = Decimal("0")

        for item in bids:
            try:
                price = Decimal(str(item["price"]))
                volume = Decimal(str(item["volume"]))
            except (KeyError, TypeError, InvalidOperation):
                continue

            if volume < min_order_volume:
                continue

            rows.append((price, volume))
            total_volume += volume

            if total_volume >= min_total_volume:
                break

        if not rows:
            return f"{self._title_bids()}\n\nНет значимых ордеров (все < 1000)."

        price_width = max(len("Цена"), *(len(self._fmt_num(p)) for p, _ in rows))
        volume_width = max(len("Объём"), *(len(self._fmt_num(v)) for _, v in rows))

        header = f"{'Цена'.ljust(price_width)} | {'Объём'.rjust(volume_width)}"
        sep = "-" * (price_width + 3 + volume_width)

        lines = [
            self._title_bids(),
            header,
            sep,
        ]

        for price, volume in rows:
            lines.append(
                f"{self._fmt_num(price).rjust(price_width)} | {self._fmt_num(volume).rjust(volume_width)}"
            )

        lines += [
            sep,
            f"Всего объём: {self._fmt_num(total_volume)}",
            f"Количество ордеров: {len(rows)}",
        ]

        return "\n".join(lines)

    def build_first_bid_text(self) -> str:
        if not self._is_ws_available():
            return f"{self._title_bid()}\n\n{self._disabled_text()}"

        try:
            bids = self.ws_service.get_bids()
        except Exception as e:  # noqa: BLE001 — защитная граница к внешнему ws_service
            log.warning("Failed to get bids from ws_service: %r", e)
            return f"{self._title_bid()}\n\nСтакан {self.exchange_name} пока недоступен."

        if not bids:
            return f"{self._title_bid()}\n\nСтакан {self.exchange_name} пока недоступен."

        try:
            first = bids[0]
            price = Decimal(str(first["price"]))
            volume = Decimal(str(first["volume"]))
        except (KeyError, IndexError, TypeError, InvalidOperation):
            return f"{self._title_bid()}\n\nСтакан {self.exchange_name} пока недоступен."

        return (
            f"{self._title_bid()}\n\n"
            f"Цена: {self._fmt_num(price)}\n"
            f"Объём: {self._fmt_num(volume)}"
        )

    def build_live_text(
        self,
        *,
        min_total_volume: Decimal = Decimal("500000"),
        min_order_volume: Decimal = Decimal("1000"),
    ) -> str:
        updated_at = self._fmt_updated_at()

        return (
            f"{self.build_asks_depth_text(min_total_volume=min_total_volume, min_order_volume=min_order_volume)}\n\n"
            f"{self.build_bids_depth_text(min_total_volume=min_total_volume, min_order_volume=min_order_volume)}\n\n"
            f"Актуально на {updated_at}"
        )

    async def refresh_live_message(self) -> None:
        if not self._live_chat_id or not self._live_message_id:
            return

        if self._refresh_lock.locked():
            self._pending_refresh = True
            return

        loop = asyncio.get_running_loop()

        try:
            async with self._refresh_lock:
                while self._live_chat_id and self._live_message_id:
                    self._pending_refresh = False

                    # Троттлинг: не чаще одной правки в MIN_REFRESH_INTERVAL_SECONDS,
                    # плюс уважаем активный retry_after от Telegram. Ждём под локом —
                    # параллельные апдейты просто выставят _pending_refresh и выйдут.
                    now = loop.time()
                    wait_until = max(
                        self._retry_after_until,
                        self._last_edit_at + self.MIN_REFRESH_INTERVAL_SECONDS,
                    )
                    if now < wait_until:
                        await asyncio.sleep(wait_until - now)

                    text = self.build_live_text()

                    result = await self.live_message_editor.edit_text(
                        chat_id=self._live_chat_id,
                        message_id=self._live_message_id,
                        text=text,
                    )

                    if result.status in {
                        LiveMessageEditStatus.UPDATED,
                        LiveMessageEditStatus.UNCHANGED,
                    }:
                        self._last_edit_at = loop.time()
                        return

                    if result.status is LiveMessageEditStatus.RETRY:
                        retry_after = max(float(result.retry_after_seconds or 0), 1.0)
                        self._retry_after_until = loop.time() + retry_after
                        self._pending_refresh = True
                        log.warning(
                            "Telegram rate limit while refreshing live message "
                            "chat_id=%s message_id=%s; retry after %.1fs",
                            self._live_chat_id,
                            self._live_message_id,
                            retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    if result.status is LiveMessageEditStatus.MISSING:
                        await self.clear_live_message()
                        return

                    log.warning(
                        "Failed to refresh live message chat_id=%s message_id=%s",
                        self._live_chat_id,
                        self._live_message_id,
                    )
                    return
        finally:
            if self._pending_refresh and self._live_chat_id and self._live_message_id:
                await asyncio.create_task(self.refresh_live_message())
