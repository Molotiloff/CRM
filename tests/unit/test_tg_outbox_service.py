from __future__ import annotations

from datetime import UTC, datetime

import pytest

from db_asyncpg.repositories.tg_outbox import TgOutboxItem
from services.tg_outbox import DealTelegramSyncService
from tests.fakes import FakeMessenger


@pytest.mark.asyncio
async def test_exchange_status_edits_both_cards_and_notifies_client() -> None:
    messenger = FakeMessenger()
    service = DealTelegramSyncService(
        repository=FakeDeliveryRepository(),
        messenger=messenger,
    )

    await service.deliver(_item(status="fixed", payload={"rate": "95.5"}))

    assert {(edit.chat_id, edit.message_id) for edit in messenger.edits} == {
        (-100500, 101),
        (-777001, 202),
    }
    assert all(edit.preserve_reply_markup for edit in messenger.edits)
    assert all("Курс зафиксирован" in (edit.text or "") for edit in messenger.edits)
    assert len(messenger.sent_to(-100500)) == 1
    assert "Новый статус" in messenger.sent_to(-100500)[0].text


@pytest.mark.asyncio
async def test_terminal_status_strips_card_keyboards() -> None:
    messenger = FakeMessenger()
    service = DealTelegramSyncService(
        repository=FakeDeliveryRepository(),
        messenger=messenger,
    )

    await service.deliver(_item(status="done"))

    assert messenger.edits
    assert all(not edit.preserve_reply_markup for edit in messenger.edits)
    client_edit = next(edit for edit in messenger.edits if edit.chat_id == -100500)
    request_edit = next(edit for edit in messenger.edits if edit.chat_id == -777001)
    assert "<b>Статус</b>: <code>Проведена</code>" in (client_edit.text or "")
    assert "Статус CRM" not in (client_edit.text or "")
    assert "Сделка завершена" in (request_edit.text or "")


@pytest.mark.asyncio
async def test_cash_status_uses_saved_card_texts() -> None:
    messenger = FakeMessenger()
    service = DealTelegramSyncService(
        repository=FakeCashDeliveryRepository(),
        messenger=messenger,
    )

    await service.deliver(_item(status="awaiting_payment"))

    assert {(edit.chat_id, edit.message_id) for edit in messenger.edits} == {
        (-100500, 303),
        (-777101, 404),
    }
    assert all("Ожидаем оплату" in (edit.text or "") for edit in messenger.edits)
    assert len(messenger.sent_to(-100500)) == 1


@pytest.mark.asyncio
async def test_cash_completion_notification_contains_actual_receipt_amount() -> None:
    messenger = FakeMessenger()
    service = DealTelegramSyncService(
        repository=FakeCashDeliveryRepository(),
        messenger=messenger,
    )

    await service.deliver(
        _item(
            status="done",
            payload={
                "cashSettlementId": 9,
                "actualQty": "125",
                "currency": "USD",
            },
        )
    )

    client_edit = next(edit for edit in messenger.edits if edit.chat_id == -100500)
    request_edit = next(edit for edit in messenger.edits if edit.chat_id == -777101)
    assert "<b>Статус</b>: <code>Проведена</code>" in (client_edit.text or "")
    assert "Статус CRM" not in (client_edit.text or "")
    assert "Фактически проведено" not in (client_edit.text or "")
    assert "Фактически проведено" in (request_edit.text or "")
    assert "125 USD" in messenger.sent_to(-100500)[0].text


@pytest.mark.asyncio
async def test_cash_ready_status_is_short_in_client_card() -> None:
    messenger = FakeMessenger()
    service = DealTelegramSyncService(
        repository=FakeCashDeliveryRepository(),
        messenger=messenger,
    )

    await service.deliver(_item(status="ready_for_cash_settlement"))

    client_edit = next(edit for edit in messenger.edits if edit.chat_id == -100500)
    request_edit = next(edit for edit in messenger.edits if edit.chat_id == -777101)
    assert "<b>Статус</b>: <code>Готово к расчету</code>" in (
        client_edit.text or ""
    )
    assert "Статус CRM" not in (client_edit.text or "")
    assert "Готово к расчету" in (request_edit.text or "")


@pytest.mark.asyncio
async def test_source_update_refreshes_cards_without_status_notification() -> None:
    messenger = FakeMessenger()
    service = DealTelegramSyncService(
        repository=FakeDeliveryRepository(),
        messenger=messenger,
    )

    await service.deliver(_item(status="fixed", kind="deal_source_updated"))

    assert {(edit.chat_id, edit.message_id) for edit in messenger.edits} == {
        (-100500, 101),
        (-777001, 202),
    }
    assert messenger.sent == []


@pytest.mark.asyncio
async def test_balance_shortage_notifies_request_chat() -> None:
    messenger = FakeMessenger()
    service = DealTelegramSyncService(
        repository=FakeDeliveryRepository(),
        messenger=messenger,
    )

    await service.deliver(
        _item(
            status="balance_check",
            payload={
                "insufficientUsdt": True,
                "actCurrentUsdt": "-25",
                "shortageUsdt": "25",
                "requestChatId": -777001,
            },
        )
    )

    request_notifications = messenger.sent_to(-777001)
    assert len(request_notifications) == 1
    assert "На откуп" in request_notifications[0].text
    assert "25 USDT" in request_notifications[0].text


class FakeDeliveryRepository:
    async def get_deal_delivery_context(self, deal_id: int):
        assert deal_id == 7
        return {
            "id": 7,
            "deal_no": 100007,
            "source_kind": "exchange",
            "client_chat_id": -100500,
            "exchange_client_chat_id": -100500,
            "exchange_client_message_id": 101,
            "exchange_request_chat_id": -777001,
            "exchange_request_message_id": 202,
            "exchange_request_text": "<b>Заявка</b>: <code>12345678</code>",
            "body": {
                "client_req_id": "12345678",
                "recv_code": "USDT",
                "recv_amount": "100.00",
                "pay_code": "RUB",
                "pay_amount": "9550.00",
                "rate": "95.5",
            },
        }


class FakeCashDeliveryRepository:
    async def get_deal_delivery_context(self, deal_id: int):
        assert deal_id == 7
        return {
            "id": 7,
            "deal_no": 100007,
            "source_kind": "cash",
            "client_chat_id": -100500,
            "cash_request_chat_id": -777101,
            "cash_request_message_id": 404,
            "body": {
                "req_id": "Б-123456",
                "telegram_client_chat_id": -100500,
                "telegram_client_message_id": 303,
                "telegram_client_text": "Заявка на внесение: Б-123456",
                "telegram_request_text": "Заявка на внесение: Б-123456\nКлиент: Тест",
            },
        }


def _item(
    *,
    status: str,
    payload: dict | None = None,
    kind: str = "deal_status_changed",
) -> TgOutboxItem:
    return TgOutboxItem(
        id=1,
        kind=kind,
        payload={
            "dealId": 7,
            ("newStatus" if kind == "deal_status_changed" else "status"): status,
            "eventPayload": payload or {},
        },
        attempts=1,
        created_at=datetime.now(UTC),
    )
