from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.schedule_coordinator import CashScheduleCoordinator, ScheduleBoardSync
from services.cash_requests.workflow_models import CashRequestResult, CashRequestStatusCommand
from services.messaging import MessengerError, MessengerPort, ReplierPort

log = logging.getLogger(__name__)

_RE_STATUS_LINE = re.compile(
    r"^\s*[✅❌]?\s*Сделка\s+(?:проведена|отменена)\s*:\s*(?:<code>)?.+?(?:</code>)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


class CashDealStatus(StrEnum):
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CashDealStatusPolicy:
    status: CashDealStatus
    line_label: str
    icon: str
    alert: str
    removed_alert: str | None = None

    def result_alert(self, *, removed: bool) -> str:
        if removed and self.removed_alert:
            return self.removed_alert
        return self.alert


DONE_POLICY = CashDealStatusPolicy(
    status=CashDealStatus.DONE,
    line_label="Сделка проведена",
    icon="✅",
    alert="Сделка проведена",
    removed_alert="Сделка завершена",
)
CANCEL_POLICY = CashDealStatusPolicy(
    status=CashDealStatus.CANCELLED,
    line_label="Сделка отменена",
    icon="❌",
    alert="Сделка отменена",
)


class CashDealStatusCardPresenter:
    def apply(self, text: str, policy: CashDealStatusPolicy) -> str:
        source = text or ""
        line = (
            f"{policy.icon} {policy.line_label}: "
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M')}</code>"
        )
        if _RE_STATUS_LINE.search(source):
            return _RE_STATUS_LINE.sub(line, source)
        marker = "\n----\nСоздал"
        index = source.find(marker)
        if index != -1:
            return source[:index] + "\n" + line + source[index:]
        separator = "" if source.endswith("\n") else "\n"
        return source + separator + line


class CashDealStatusWorkflow:
    def __init__(
        self,
        *,
        router_service: RequestRouterService,
        schedule_coordinator: CashScheduleCoordinator,
        card_presenter: CashDealStatusCardPresenter | None = None,
    ) -> None:
        self._router = router_service
        self._schedule = schedule_coordinator
        self._cards = card_presenter or CashDealStatusCardPresenter()

    async def execute(
        self,
        command: CashRequestStatusCommand,
        *,
        req_id: str,
        policy: CashDealStatusPolicy,
        messenger: MessengerPort,
        replier: ReplierPort,
        sync_schedule_board: ScheduleBoardSync | None = None,
    ) -> CashRequestResult:
        city = self._router.city_by_request_chat(command.chat_id)
        if not city:
            error = "Не удалось определить город."
            await replier.alert(error)
            return CashRequestResult(ok=False, req_id=req_id, error=error)

        removed = await self._schedule.remove(
            req_id=req_id,
            city=city,
            sync_board=sync_schedule_board,
        )
        new_text = self._cards.apply(command.card_text, policy)
        try:
            if command.is_caption:
                await messenger.edit_caption(
                    chat_id=command.chat_id,
                    message_id=command.message_id,
                    caption=new_text,
                    reply_markup=None,
                )
            else:
                await messenger.edit_text(
                    chat_id=command.chat_id,
                    message_id=command.message_id,
                    text=new_text,
                    reply_markup=None,
                )
        except MessengerError as exc:
            log.debug("Cash deal status card edit failed (%s); stripping keyboard", exc)
            try:
                await messenger.edit_reply_markup(
                    chat_id=command.chat_id,
                    message_id=command.message_id,
                    reply_markup=None,
                )
            except MessengerError as strip_error:
                if not strip_error.benign:
                    raise
                log.debug("Cash deal status keyboard strip skipped: %s", strip_error)

        await replier.alert(policy.result_alert(removed=removed), modal=False)
        return CashRequestResult(
            ok=True,
            req_id=req_id,
            removed_from_schedule=removed,
        )
