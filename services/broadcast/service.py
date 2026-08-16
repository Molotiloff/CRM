from __future__ import annotations

from typing import Any

from db_asyncpg.ports.clients import ClientRepositoryPort
from services.broadcast.delivery_port import BroadcastDeliveryPort
from services.broadcast.models import (
    BroadcastCommand,
    BroadcastDeliveryStatus,
    BroadcastResult,
)


class BroadcastService:
    def __init__(self, *, repo: ClientRepositoryPort) -> None:
        self.repo = repo

    @staticmethod
    def extract_group_from_command(text: str) -> str | None:
        parts = (text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            return None

        group = parts[1].strip()
        return group or None

    @staticmethod
    def target_label(group: str | None) -> str:
        if group:
            return f"для группы «{group}»"
        return "для всех клиентов"

    async def get_target_clients(self, *, group: str | None = None) -> list[dict[str, Any]]:
        clients = await self.repo.list_clients()
        if not group:
            return clients

        group_norm = group.strip().casefold()
        return [
            c for c in clients
            if str(c.get("client_group") or "").strip().casefold() == group_norm
        ]

    async def send(
        self,
        command: BroadcastCommand,
        *,
        delivery: BroadcastDeliveryPort,
    ) -> BroadcastResult:
        clients = await self.get_target_clients(group=command.target_group)
        counters = dict.fromkeys(BroadcastDeliveryStatus, 0)

        for client in clients:
            chat_id = client.get("chat_id")
            if not chat_id:
                counters[BroadcastDeliveryStatus.SKIPPED] += 1
                continue

            status = await delivery.send(chat_id=int(chat_id))
            counters[status] += 1

        return BroadcastResult(
            sent=counters[BroadcastDeliveryStatus.SENT],
            blocked=counters[BroadcastDeliveryStatus.BLOCKED],
            not_found=counters[BroadcastDeliveryStatus.NOT_FOUND],
            skipped=counters[BroadcastDeliveryStatus.SKIPPED],
            failed=counters[BroadcastDeliveryStatus.FAILED],
        )

    @staticmethod
    def empty_clients_text(group: str | None) -> str:
        if group:
            return f"Нет активных клиентов в группе «{group}»."
        return "Нет активных клиентов для рассылки."

    @staticmethod
    def result_text(result: BroadcastResult) -> str:
        return (
            "📣 <b>Рассылка завершена</b>\n\n"
            f"✅ Отправлено: <b>{result.sent}</b>\n"
        )
