from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from handlers.broadcast_all import BroadcastAllHandler
from services.broadcast import (
    BroadcastCommand,
    BroadcastDeliveryStatus,
    BroadcastService,
)
from telegram_adapters.broadcast_session_store import AiogramBroadcastSessionStore


class ClientRepoStub:
    def __init__(self, clients: list[dict[str, object]]) -> None:
        self.clients = clients

    async def list_clients(self) -> list[dict[str, object]]:
        return self.clients


@dataclass
class DeliveryStub:
    statuses: dict[int, BroadcastDeliveryStatus]

    async def send(self, *, chat_id: int) -> BroadcastDeliveryStatus:
        return self.statuses[chat_id]


async def test_send_filters_group_and_counts_delivery_outcomes() -> None:
    service = BroadcastService(
        repo=ClientRepoStub(
            [
                {"chat_id": 10, "client_group": "Office"},
                {"chat_id": 20, "client_group": "office"},
                {"chat_id": 30, "client_group": "Other"},
                {"chat_id": None, "client_group": "Office"},
            ]
        )
    )
    delivery = DeliveryStub(
        {
            10: BroadcastDeliveryStatus.SENT,
            20: BroadcastDeliveryStatus.BLOCKED,
        }
    )

    result = await service.send(
        BroadcastCommand(target_group=" OFFICE "),
        delivery=delivery,
    )

    assert result.sent == 1
    assert result.blocked == 1
    assert result.not_found == 0
    assert result.skipped == 1
    assert result.failed == 0
    assert result.attempted == 2


async def test_silent_accounting_chats_are_excluded_from_broadcast() -> None:
    service = BroadcastService(
        repo=ClientRepoStub(
            [
                {"chat_id": -100, "client_group": None},
                {"chat_id": -200, "client_group": None},
            ]
        ),
        excluded_chat_ids={-200},
    )
    delivery = DeliveryStub({-100: BroadcastDeliveryStatus.SENT})

    result = await service.send(BroadcastCommand(), delivery=delivery)

    assert result.sent == 1
    assert result.attempted == 1


def test_command_parser_and_target_text_are_transport_neutral() -> None:
    assert BroadcastService.extract_group_from_command("/всем partners") == "partners"
    assert BroadcastService.extract_group_from_command("/всем") is None
    assert BroadcastService.target_label("partners") == "для группы «partners»"
    assert BroadcastService.empty_clients_text(None) == "Нет активных клиентов для рассылки."


def test_broadcast_reply_filter_does_not_consume_transfer_receipts() -> None:
    store = AiogramBroadcastSessionStore()
    handler = object.__new__(BroadcastAllHandler)
    handler.session_store = store
    handler.admin_chat_ids = {-100}

    def reply(chat_id: int, message_id: int):
        return SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            reply_to_message=SimpleNamespace(message_id=message_id),
        )

    assert not handler._is_broadcast_reply(reply(-200, 599))
    assert not handler._is_broadcast_reply(reply(-100, 599))
    store.add_prompt(chat_id=-100, prompt_message_id=42, group=None)
    assert handler._is_broadcast_reply(reply(-100, 42))
    assert not handler._is_broadcast_reply(reply(-100, 599))
