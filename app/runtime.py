from __future__ import annotations

import logging
from dataclasses import dataclass, field

from aiogram import Bot

from config import Config
from services.aml import AMLQueueService
from services.lifecycle import (
    AsyncLifecycle,
    LifecycleComponent,
    LifecycleSupervisor,
)
from services.payment_watch import PaymentWatchPoller
from services.rate_order import OrderbookService, RapiraWsService, RateOrderService
from services.tg_outbox import TgOutboxWorker

log = logging.getLogger(__name__)


@dataclass(slots=True)
class BotRuntimeServices:
    daily_balances_scheduler: AsyncLifecycle | None = None
    market_ws_service: RapiraWsService | None = None
    orderbook_service: OrderbookService | None = None
    rate_order_service: RateOrderService | None = None
    aml_queue_service: AMLQueueService | None = None
    payment_watch_poller: PaymentWatchPoller | None = None
    tg_outbox_worker: TgOutboxWorker | None = None
    _supervisor: LifecycleSupervisor | None = field(default=None, init=False)

    async def start(self, *, bot: Bot, config: Config) -> None:
        del bot
        if self._supervisor and self._supervisor.running:
            return
        if self.orderbook_service:
            await self.orderbook_service.restore_live_message(
                admin_chat_id=config.admin_chat_id,
            )
            log.info("Rapira live orderbook message restored")
        supervisor = LifecycleSupervisor(self._lifecycle_components())
        self._supervisor = supervisor
        try:
            await supervisor.start()
        except BaseException:
            self._supervisor = None
            raise

    async def stop(self) -> None:
        supervisor = self._supervisor
        if supervisor is None:
            return
        try:
            await supervisor.stop()
        finally:
            self._supervisor = None

    def _lifecycle_components(self) -> tuple[LifecycleComponent, ...]:
        candidates: tuple[tuple[str, AsyncLifecycle | None], ...] = (
            ("Daily balances scheduler", self.daily_balances_scheduler),
            ("AML queue service", self.aml_queue_service),
            ("Payment watch poller", self.payment_watch_poller),
            ("Telegram outbox worker", self.tg_outbox_worker),
            ("Rapira websocket service", self.market_ws_service),
        )
        return tuple(
            LifecycleComponent(name, component) for name, component in candidates if component
        )
