from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from db_asyncpg.ports.clients import ClientRepositoryPort
from services.broadcast.delivery_port import BroadcastDeliveryPort
from services.broadcast.models import (
    BroadcastCommand,
    BroadcastDeliveryStatus,
    BroadcastResult,
)


class InactiveBroadcastAudience(Protocol):
    async def list_inactive_clients(
        self,
        *,
        cutoff: datetime,
        staff_user_ids: list[int],
    ) -> list[dict[str, Any]]: ...


class BroadcastService:
    INACTIVE_TARGET = "неактивным"
    INACTIVE_EXCLUDED_GROUPS = frozenset(
        {"балансы", "партнер", "партнёр", "кит", "internal_wallet"}
    )

    def __init__(
        self,
        *,
        repo: ClientRepositoryPort,
        excluded_chat_ids: Iterable[int] | None = None,
        inactive_audience: InactiveBroadcastAudience | None = None,
        staff_user_ids: Iterable[int] = (),
    ) -> None:
        self.repo = repo
        self.excluded_chat_ids = {int(chat_id) for chat_id in excluded_chat_ids or []}
        self.inactive_audience = inactive_audience
        self.staff_user_ids = sorted(set(staff_user_ids))

    @classmethod
    def is_inactive_target(cls, group: str | None) -> bool:
        return (group or "").strip().casefold() == cls.INACTIVE_TARGET

    @staticmethod
    def extract_group_from_command(text: str) -> str | None:
        parts = (text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            return None

        group = parts[1].strip()
        return group or None

    @staticmethod
    def target_label(group: str | None) -> str:
        if BroadcastService.is_inactive_target(group):
            return "для клиентов, которые не писали больше недели"
        if group:
            return f"для группы «{group}»"
        return "для всех клиентов"

    async def get_target_clients(self, *, group: str | None = None) -> list[dict[str, Any]]:
        if self.is_inactive_target(group):
            if self.inactive_audience is None:
                return []
            clients = await self.inactive_audience.list_inactive_clients(
                cutoff=datetime.now(UTC) - timedelta(days=7),
                staff_user_ids=self.staff_user_ids,
            )
            return [
                client
                for client in clients
                if (chat_id := _chat_id_or_none(client.get("chat_id")))
                and chat_id not in self.excluded_chat_ids
                and str(client.get("client_group") or "").strip().casefold()
                not in self.INACTIVE_EXCLUDED_GROUPS
            ]
        clients = await self.repo.list_clients()
        clients = [
            client
            for client in clients
            if _chat_id_or_none(client.get("chat_id")) not in self.excluded_chat_ids
        ]
        if not group:
            return clients

        group_norm = group.strip().casefold()
        return [
            c for c in clients if str(c.get("client_group") or "").strip().casefold() == group_norm
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
        if BroadcastService.is_inactive_target(group):
            return "По архиву не найдено клиентов, которые не писали больше недели."
        if group:
            return f"Нет активных клиентов в группе «{group}»."
        return "Нет активных клиентов для рассылки."

    @staticmethod
    def result_text(result: BroadcastResult) -> str:
        return f"📣 <b>Рассылка завершена</b>\n\n✅ Отправлено: <b>{result.sent}</b>\n"


def _chat_id_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
