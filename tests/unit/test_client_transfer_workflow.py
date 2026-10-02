from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.crm.client_transfer_models import ClientTransferCommand, ClientTransferResult
from services.crm.client_transfer_workflow import ClientTransferWorkflow
from tests.fakes import FakeMessenger


class TransferServiceStub:
    def __init__(self, result: ClientTransferResult) -> None:
        self.result = result
        self.command: ClientTransferCommand | None = None

    async def transfer(self, command: ClientTransferCommand) -> ClientTransferResult:
        self.command = command
        return self.result


def _result() -> ClientTransferResult:
    return ClientTransferResult(
        deal_id=42,
        from_client_name="Отправитель",
        from_chat_id=-700001,
        to_client_name="Получатель",
        to_chat_id=-700002,
        from_balance=Decimal("75"),
        to_balance=Decimal("25"),
        amount=Decimal("25"),
        currency="RUB",
        precision=2,
        created_at=datetime.now(UTC),
        repeated=False,
    )


@pytest.mark.asyncio
async def test_transfer_workflow_sends_receipt_and_balance_to_both_chats() -> None:
    service = TransferServiceStub(_result())
    messenger = FakeMessenger()
    command = ClientTransferCommand(
        from_client_id=1,
        to_client_id=2,
        amount=Decimal("25"),
        currency="RUB",
        source="crm",
        source_ref="key-1",
        city="внутренний",
    )

    result = await ClientTransferWorkflow(service, messenger).transfer(command)

    assert result.deal_id == 42
    assert service.command is command
    sender = messenger.sent_to(-700001)
    recipient = messenger.sent_to(-700002)
    assert [item.text for item in sender] == [
        "Перевод #42",
        "Перевод #42 проведён.\nПолучатель: Получатель\n"
        "Списано: 25.00 RUB\nБаланс: 75.00 RUB",
    ]
    assert [item.text for item in recipient] == [
        "Перевод #42",
        "Перевод #42 от Отправитель.\nЗачислено: 25.00 RUB\nБаланс: 25.00 RUB",
    ]


@pytest.mark.asyncio
async def test_repeated_transfer_does_not_resend_receipts_or_recipient_notice() -> None:
    service = TransferServiceStub(replace(_result(), repeated=True))
    messenger = FakeMessenger()
    command = ClientTransferCommand(
        from_client_id=1,
        to_client_id=2,
        amount=Decimal("25"),
        currency="RUB",
        source="crm",
        source_ref="key-1",
        city="внутренний",
    )

    await ClientTransferWorkflow(service, messenger).transfer(command)

    assert len(messenger.sent_to(-700001)) == 1
    assert "(повтор)" in messenger.sent_to(-700001)[0].text
    assert messenger.sent_to(-700002) == []
