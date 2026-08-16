from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from services.cash_requests.models import ScheduleEntry
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService

log = logging.getLogger(__name__)

ScheduleBoardSync = Callable[[str], Awaitable[None]]


class CashScheduleCoordinator:
    def __init__(
        self,
        *,
        router_service: RequestRouterService,
        schedule_service: RequestScheduleService,
    ) -> None:
        self._router = router_service
        self._schedule = schedule_service

    async def upsert(
        self,
        entry: ScheduleEntry,
        *,
        sync_board: ScheduleBoardSync | None = None,
    ) -> None:
        await self._schedule.upsert_entry(entry)
        await self.sync(entry.city, sync_board=sync_board)

    async def remove(
        self,
        *,
        req_id: str,
        city: str,
        sync_board: ScheduleBoardSync | None = None,
    ) -> bool:
        removed = await self._schedule.remove_entry(req_id=req_id)
        if removed:
            await self.sync(city, sync_board=sync_board)
        return removed

    async def sync(
        self,
        city: str,
        *,
        sync_board: ScheduleBoardSync | None,
    ) -> None:
        if not sync_board or not self._router.pick_schedule_chat_for_city(city):
            return
        try:
            await sync_board(city)
        except Exception:
            log.exception("Failed to sync schedule board for city %s", city)
