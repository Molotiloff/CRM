from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from domain import SettlementReviewStatus
from services.payment_watch import (
    PaymentWatchPoller,
    PaymentWatchService,
    StartPaymentWatchCommand,
)
from services.payment_watch.address_parser import extract_tron_address
from services.payment_watch.models import PaymentWatchNotification, TronTransfer
from services.payment_watch.settlement_models import SettlementResult

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


class ClientWithdrawalStub:
    def __init__(self) -> None:
        self.command = None

    async def start_client_withdrawal(self, command):
        self.command = command
        return type("Started", (), {"watch_id": 42})()


class TronscanGatewayStub:
    async def aclose(self) -> None:
        pass


async def test_start_watch_uses_transport_neutral_command() -> None:
    repo = StartWatchRepoStub()
    withdrawal = ClientWithdrawalStub()
    service = PaymentWatchService(
        repo=repo,
        tronscan_gateway=TronscanGatewayStub(),
        fulfillment_queue_service=withdrawal,
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
            command_message_id=201,
        )
    )

    assert started.watch_id == 42
    assert TRON_ADDRESS in started.message_text
    assert withdrawal.command.chat_id == 100
    assert withdrawal.command.reply_message_id == 200
    assert withdrawal.command.command_message_id == 201
    assert withdrawal.command.requested_qty == Decimal("10000")
    assert withdrawal.command.mode == "TEST_THEN_MAIN"
    assert withdrawal.command.phase == "TEST"


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


class PollRepoStub:
    async def get_payment_watch_event_hashes(self, *, watch_id: int) -> set[str]:
        return set()


class TransferGatewayStub(TronscanGatewayStub):
    async def list_usdt_transfers(self, **_: object) -> list[TronTransfer]:
        return [
            TronTransfer(
                tx_hash="tx-after-commit",
                from_address=OUR_ADDRESS,
                to_address=TRON_ADDRESS,
                amount=Decimal("100"),
                token_symbol="USDT",
                block_number=1,
                block_ts=datetime.now(UTC),
                confirmations=1,
                confirmed=True,
            )
        ]


class ReverseTransferGatewayStub(TronscanGatewayStub):
    async def list_usdt_transfers(self, **_: object) -> list[TronTransfer]:
        return [
            TronTransfer(
                tx_hash="reverse-transfer",
                from_address=TRON_ADDRESS,
                to_address=OUR_ADDRESS,
                amount=Decimal("100"),
                token_symbol="USDT",
                block_number=1,
                block_ts=datetime.now(UTC),
                confirmations=1,
                confirmed=True,
            )
        ]


class SettlementServiceStub:
    def __init__(self, *, wallet_amount: Decimal | None = None) -> None:
        self.committed = False
        self.wallet_amount = wallet_amount

    async def settle(self, transfer) -> SettlementResult:
        self.committed = True
        return SettlementResult(
            settlement_id=1,
            event_id=2,
            deal_id=3,
            expected=transfer.amount,
            actual=transfer.amount,
            delta=Decimal("0"),
            status=SettlementReviewStatus.MATCHED,
            created=True,
            evidence=transfer,
            client_wallet_amount=self.wallet_amount,
            client_wallet_balance_after=Decimal("42.15") if self.wallet_amount is not None else None,
            client_wallet_precision=2 if self.wallet_amount is not None else None,
        )


class ReceiptBuilderStub:
    def __init__(self, settlement: SettlementServiceStub) -> None:
        self._settlement = settlement

    def build_main_success(self, **_: object) -> bytes:
        assert self._settlement.committed is True
        return b"receipt"


async def test_main_receipt_is_built_only_after_settlement_commit() -> None:
    settlement = SettlementServiceStub()
    service = PaymentWatchService(
        repo=PollRepoStub(),
        tronscan_gateway=TransferGatewayStub(),
        settlement_service=settlement,
    )
    service.receipt_builder = ReceiptBuilderStub(settlement)

    notifications = await service._process_watch(
        {
            "id": 10,
            "address": TRON_ADDRESS,
            "our_address": OUR_ADDRESS,
            "phase": "MAIN",
            "mode": "SINGLE",
            "started_at": datetime.now(UTC),
            "chat_id": 20,
            "reply_message_id": 30,
            "notice_message_id": None,
        }
    )

    assert notifications[0].photo_bytes == b"receipt"


async def test_otpr_sends_balance_as_separate_message_after_receipt() -> None:
    settlement = SettlementServiceStub(wallet_amount=Decimal("-190"))
    service = PaymentWatchService(
        repo=PollRepoStub(),
        tronscan_gateway=TransferGatewayStub(),
        settlement_service=settlement,
    )
    service.receipt_builder = ReceiptBuilderStub(settlement)

    notifications = await service._process_watch(
        {
            "id": 10,
            "address": TRON_ADDRESS,
            "our_address": OUR_ADDRESS,
            "phase": "MAIN",
            "mode": "SINGLE",
            "started_at": datetime.now(UTC),
            "chat_id": 20,
            "reply_message_id": 30,
            "notice_message_id": None,
        }
    )

    assert len(notifications) == 2
    assert notifications[0].photo_bytes == b"receipt"
    assert notifications[1].photo_bytes is None
    assert notifications[1].reply_message_id is None
    assert notifications[1].text == "Запомнил. -190.00\nБаланс: 42.15 usdt"


async def test_otpr_processes_reverse_transfer_as_incoming() -> None:
    settlement = SettlementServiceStub(wallet_amount=Decimal("190"))
    service = PaymentWatchService(
        repo=PollRepoStub(),
        tronscan_gateway=ReverseTransferGatewayStub(),
        settlement_service=settlement,
    )

    notifications = await service._process_watch(
        {
            "id": 10,
            "address": TRON_ADDRESS,
            "our_address": OUR_ADDRESS,
            "phase": "MAIN",
            "mode": "SINGLE",
            "started_at": datetime.now(UTC),
            "chat_id": 20,
            "reply_message_id": 30,
            "notice_message_id": None,
        }
    )

    assert len(notifications) == 2
    assert settlement.committed is True
    assert "Средства получены" in notifications[0].text
    assert notifications[1].text == "Запомнил. +190.00\nБаланс: 42.15 usdt"
