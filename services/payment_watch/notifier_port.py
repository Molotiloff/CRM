from __future__ import annotations

from typing import Protocol

from services.payment_watch.models import PaymentWatchNotification


class PaymentWatchNotifierPort(Protocol):
    async def deliver(self, notification: PaymentWatchNotification) -> int: ...

