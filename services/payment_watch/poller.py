from __future__ import annotations

import asyncio
import logging

from services.lifecycle import ManagedTaskLifecycle
from services.payment_watch.models import PaymentWatchNotification
from services.payment_watch.notifier_port import PaymentWatchNotifierPort
from services.payment_watch.service import PaymentWatchService

log = logging.getLogger("payment_watch")


class PaymentWatchPoller(ManagedTaskLifecycle):
    def __init__(
        self,
        *,
        notifier: PaymentWatchNotifierPort,
        service: PaymentWatchService,
        interval_seconds: int = 30,
    ) -> None:
        super().__init__(task_name="payment_watch_poller")
        self.notifier = notifier
        self.service = service
        self.interval_seconds = int(interval_seconds)
    async def _after_stop(self) -> None:
        await self.service.aclose()
        log.info("Payment watch poller stopped")

    async def _run(self) -> None:
        log.info("Payment watch poller started")
        while not self.is_stopping:
            try:
                notifications = await self.service.poll_once()
                for item in notifications:
                    await self._send(item)
            except Exception:
                log.exception("Payment watch poll iteration failed")
            await asyncio.sleep(self.interval_seconds)

    async def _send(self, item: PaymentWatchNotification) -> None:
        message_id = await self.notifier.deliver(item)
        # Запоминаем актуальное служебное сообщение watch'а (таймаут, тестовый
        # платёж): следующее уведомление удалит именно его, а не давно удалённое.
        if item.watch_id is not None:
            await self.service.set_notice_message_id(
                watch_id=item.watch_id,
                message_id=message_id,
            )
