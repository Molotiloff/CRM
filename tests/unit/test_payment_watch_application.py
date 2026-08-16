from __future__ import annotations

import asyncio

from services.payment_watch import (
    PaymentWatchPoller,
    PaymentWatchService,
    StartPaymentWatchCommand,
)
from services.payment_watch.address_parser import extract_tron_address
from services.payment_watch.models import PaymentWatchNotification

TRON_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
OUR_ADDRESS = "TVjsyZ7fYF3qLF6BQgPmTEZy1xrNNyVAAA"


class StartWatchRepoStub:
    def __init__(self) -> None:
        self.created: dict[str, object] | None = None

    async def get_setting(self, key: str) -> str:
        return OUR_ADDRESS

    async def get_active_payment_watch_by_reply(
        self,
        *,
        chat_id: int,
        reply_message_id: int,
    ) -> None:
        return None

    async def create_payment_watch(self, **kwargs: object) -> int:
        self.created = kwargs
        return 42


class TronscanGatewayStub:
    async def aclose(self) -> None:
        pass


async def test_start_watch_uses_transport_neutral_command() -> None:
    repo = StartWatchRepoStub()
    service = PaymentWatchService(
        repo=repo,
        tronscan_gateway=TronscanGatewayStub(),
    )

    started = await service.start_watch(
        StartPaymentWatchCommand(
            chat_id=100,
            chat_name="Client chat",
            reply_message_id=200,
            reply_text=f"Кошелёк: {TRON_ADDRESS}",
            reply_caption=None,
            created_by_user_id=300,
            test_mode=True,
            manager_note="10000",
        )
    )

    assert started.watch_id == 42
    assert TRON_ADDRESS in started.message_text
    assert repo.created is not None
    assert repo.created["chat_id"] == 100
    assert repo.created["reply_message_id"] == 200
    assert repo.created["mode"] == "TEST_THEN_MAIN"
    assert repo.created["phase"] == "TEST"


def test_extract_tron_address_accepts_text_or_caption() -> None:
    assert extract_tron_address("no wallet", f"USDT {TRON_ADDRESS}") == TRON_ADDRESS
    assert extract_tron_address(None, "no wallet") is None


class NotifierStub:
    def __init__(self) -> None:
        self.delivered: PaymentWatchNotification | None = None

    async def deliver(self, notification: PaymentWatchNotification) -> int:
        self.delivered = notification
        return 777


class PollServiceStub:
    def __init__(self) -> None:
        self.notice: tuple[int, int] | None = None
        self.closed = False

    async def poll_once(self) -> list[PaymentWatchNotification]:
        return []

    async def set_notice_message_id(self, *, watch_id: int, message_id: int) -> None:
        self.notice = (watch_id, message_id)

    async def aclose(self) -> None:
        self.closed = True


async def test_poller_delegates_delivery_to_notifier_port() -> None:
    notifier = NotifierStub()
    service = PollServiceStub()
    poller = PaymentWatchPoller(notifier=notifier, service=service)
    notification = PaymentWatchNotification(
        chat_id=10,
        reply_message_id=20,
        text="Payment detected",
        watch_id=30,
    )

    await poller._send(notification)

    assert notifier.delivered == notification
    assert service.notice == (30, 777)


async def test_poller_closes_payment_service_on_shutdown() -> None:
    service = PollServiceStub()
    poller = PaymentWatchPoller(
        notifier=NotifierStub(),
        service=service,
        interval_seconds=60,
    )

    await poller.start()
    await asyncio.sleep(0)
    await poller.stop()

    assert service.closed is True
