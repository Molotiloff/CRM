from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from db_asyncpg.ports.administration import ManagerRepositoryPort
from services.cash_requests.card_text_parser import (
    build_schedule_line_from_plain,
    detect_kind_from_card,
    extract_client_name,
    extract_edit_source,
    starts_with_request,
    upsert_time_line,
)
from services.cash_requests.models import ScheduleEntry
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from services.cash_requests.schedule_coordinator import CashScheduleCoordinator
from services.messaging import MessengerError, MessengerPort, ReplierPort

log = logging.getLogger(__name__)

ScheduleBoardSync = Callable[[str], Awaitable[None]]

_RE_TIME_CMD = re.compile(
    r"^/время(?:@\w+)?\s+((?:[01]?\d|2[0-3]):[0-5]\d)\s*$",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class RequestTimeParams:
    request_chat_id: int
    target_chat_id: int
    target_message_id: int
    target_text_html: str
    target_text_plain: str
    is_caption: bool
    reply_markup: object | None
    hhmm: str


@dataclass(slots=True, frozen=True)
class RequestTimeResult:
    ok: bool
    req_id: str | None = None
    error: str | None = None


class RequestTimeService:
    def __init__(
        self,
        *,
        repo: ManagerRepositoryPort,
        router_service: RequestRouterService,
        schedule_service: RequestScheduleService,
        admin_chat_ids: set[int],
        admin_user_ids: set[int],
        schedule_coordinator: CashScheduleCoordinator | None = None,
    ) -> None:
        self.repo = repo
        self.router_service = router_service
        self.schedule_service = schedule_service
        self.admin_chat_ids = set(admin_chat_ids)
        self.admin_user_ids = set(admin_user_ids)
        self.schedule_coordinator = schedule_coordinator or CashScheduleCoordinator(
            router_service=router_service,
            schedule_service=schedule_service,
        )

    @staticmethod
    def parse_time(raw: str) -> str | None:
        m = _RE_TIME_CMD.match(raw)
        if not m:
            return None

        hhmm_raw = m.group(1)
        hh, mm = hhmm_raw.split(":")
        return f"{int(hh):02d}:{mm}"

    async def execute_core(
        self,
        params: RequestTimeParams,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
        sync_schedule_board: ScheduleBoardSync | None = None,
    ) -> RequestTimeResult:
        if not starts_with_request(params.target_text_html):
            error = "Это не похоже на заявку."
            await replier.reply(error)
            return RequestTimeResult(ok=False, error=error)

        updated = upsert_time_line(params.target_text_html, params.hhmm)

        try:
            if params.is_caption:
                await messenger.edit_caption(
                    chat_id=params.target_chat_id,
                    message_id=params.target_message_id,
                    caption=updated,
                    reply_markup=params.reply_markup,
                )
            else:
                await messenger.edit_text(
                    chat_id=params.target_chat_id,
                    message_id=params.target_message_id,
                    text=updated,
                    reply_markup=params.reply_markup,
                )
        except MessengerError as e:
            if "message is not modified" in str(e).lower():
                await replier.reply("Время уже установлено.")
                return RequestTimeResult(ok=True)
            log.warning("Failed to update cash request time message: %r", e)
            error = f"Не удалось обновить заявку: {e}"
            await replier.reply(error)
            return RequestTimeResult(ok=False, error=error)

        city = self.router_service.city_by_request_chat(params.request_chat_id)
        if not city:
            error = (
                "Не удалось определить город для этого чата заявок. "
                "Проверь конфигурацию CITY_CASH_CHAT_IDS."
            )
            await replier.reply(error)
            return RequestTimeResult(ok=False, error=error)

        plain_reply = params.target_text_plain
        line_text = build_schedule_line_from_plain(plain_reply, fallback_client="—")
        request_kind = detect_kind_from_card(plain_reply) or "unknown"
        client_name = extract_client_name(plain_reply, fallback="—")
        src = extract_edit_source(plain_reply)

        if not src:
            error = "Не удалось определить номер заявки."
            await replier.reply(error)
            return RequestTimeResult(ok=False, error=error)

        if line_text:
            await self.schedule_coordinator.upsert(
                ScheduleEntry(
                    req_id=src.req_id,
                    city=city,
                    hhmm=params.hhmm,
                    request_kind=request_kind,
                    line_text=line_text,
                    client_name=client_name,
                    request_chat_id=params.target_chat_id,
                    request_message_id=params.target_message_id,
                ),
                sync_board=sync_schedule_board,
            )

        await replier.reply(f"✅ Время добавлено: {params.hhmm}")
        return RequestTimeResult(ok=True, req_id=src.req_id)
