from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from observability import measured_operation
from services.cash_requests.audit import (
    RequestAudit,
    audit_lines_for_request_chat,
    created_by_from_old_text,
)
from services.cash_requests.calculator import CashRequestCalculationError
from services.cash_requests.parsing import ParsedRequest
from services.cash_requests.request_use_case_base import (
    CashRequestUseCaseBase,
    ScheduleBoardSync,
)
from services.cash_requests.workflow_models import CashRequestCommand, CashRequestResult
from services.messaging import DeferredMessenger, MessengerError, MessengerPort, ReplierPort

log = logging.getLogger(__name__)

OldCityScheduleSync = Callable[[str], Awaitable[None]]


@dataclass(slots=True, frozen=True)
class EditCashRequestParams(CashRequestCommand):
    chat_name: str
    parsed: ParsedRequest
    old_text: str
    reply_msg_id: int
    editor_name: str = "unknown"
    client_group: str | None = None


EditCashRequestResult = CashRequestResult


class EditCashRequest(CashRequestUseCaseBase):
    async def prepare_core(
        self,
        params: EditCashRequestParams,
        *,
        replier: ReplierPort,
    ) -> EditCashRequestResult:
        return await self.execute_core(
            params,
            messenger=DeferredMessenger(),
            replier=replier,
            prepare_only=True,
        )

    @measured_operation("cash_request.edit")
    async def execute_core(
        self,
        params: EditCashRequestParams,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
        sync_schedule_board: ScheduleBoardSync | None = None,
        sync_old_city_schedule: OldCityScheduleSync | None = None,
        prepare_only: bool = False,
    ) -> EditCashRequestResult:
        parsed = params.parsed
        old_text = params.old_text
        src = self.card_parser.edit_source(old_text)
        if not src:
            error = "Не похоже на карточку заявки."
            await replier.reply(error)
            return EditCashRequestResult(ok=False, error=error)

        old_entry = await self.repo.get_request_schedule_entry_by_req_id(req_id=src.req_id)
        old_request_chat_id = (
            int(old_entry["request_chat_id"])
            if old_entry and old_entry.get("request_chat_id")
            else None
        )
        old_request_message_id = (
            int(old_entry["request_message_id"])
            if old_entry and old_entry.get("request_message_id")
            else None
        )
        old_city = (
            str(old_entry["city"]).strip().lower() if old_entry and old_entry.get("city") else None
        )

        if src.kind != parsed.kind:
            error = "Нельзя менять тип заявки при редактировании (деп/выд/обмен)."
            await replier.reply(error)
            return EditCashRequestResult(ok=False, req_id=src.req_id, error=error)

        audit = RequestAudit(
            created_by=created_by_from_old_text(old_text) or params.editor_name,
            changed_by=params.editor_name,
            changed_ts=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        ctx = await self._build_request_context_core(
            chat_id=params.chat_id,
            chat_name=params.chat_name,
            city=parsed.city,
            client_group=params.client_group,
        )
        accounts = await self.repo.snapshot_wallet(ctx.client_id)
        snapshot = self.card_parser.edit_snapshot(
            old_text,
            source=src,
            city=ctx.city,
        )
        if snapshot is None:
            error = "Не удалось распарсить исходную заявку."
            await replier.reply(error)
            return EditCashRequestResult(ok=False, req_id=src.req_id, error=error)

        try:
            details = self.calculator.calculate(
                parsed,
                accounts,
                expected_codes=snapshot.expected_codes,
            )
        except CashRequestCalculationError as exc:
            await replier.reply(str(exc))
            return EditCashRequestResult(ok=False, req_id=src.req_id, error=str(exc))

        plan = self.card_presenter.build(
            details=details,
            request_id=src.req_id,
            city=ctx.city,
            client_name=ctx.chat_name,
            pin_code=src.pin_code,
            contact1=parsed.contact1,
            contact2=parsed.contact2,
            comment=parsed.comment,
            audit_lines=audit_lines_for_request_chat(audit),
            changed=True,
        )
        text_client = plan.client_text
        text_city = plan.request_text
        city_markup = plan.request_markup
        schedule_line = plan.schedule_line

        if prepare_only:
            return EditCashRequestResult(
                ok=True,
                req_id=src.req_id,
                client_text=text_client,
                request_text=text_city,
                schedule_line=schedule_line,
            )

        try:
            await messenger.edit_text(
                chat_id=params.chat_id,
                message_id=params.reply_msg_id,
                text=text_client,
                reply_markup=None,
            )
        except MessengerError as e:
            if "message is not modified" not in str(e).lower():
                log.warning("Failed to edit cash request client message: %r", e)
                error = f"Не удалось отредактировать заявку: {e}"
                await replier.reply(error)
                return EditCashRequestResult(ok=False, req_id=src.req_id, error=error)

        if ctx.request_chat_id:
            sent_chat_id: int | None = None
            sent_message_id: int | None = None
            same_request_chat = bool(
                old_request_chat_id is not None
                and int(old_request_chat_id) == int(ctx.request_chat_id)
                and old_request_message_id is not None
            )

            if same_request_chat:
                edited_msg_ref = await self._edit_request_chat_message_core(
                    messenger=messenger,
                    req_id=src.req_id,
                    text_city=text_city,
                    city_markup=city_markup,
                )
                if edited_msg_ref:
                    sent_chat_id, sent_message_id = edited_msg_ref

            if sent_chat_id is None or sent_message_id is None:
                if (
                    old_request_chat_id is not None
                    and old_request_message_id is not None
                    and (
                        int(old_request_chat_id) != int(ctx.request_chat_id) or sent_chat_id is None
                    )
                ):
                    try:
                        await messenger.delete(
                            chat_id=int(old_request_chat_id),
                            message_id=int(old_request_message_id),
                        )
                    except MessengerError as e:
                        if not e.benign:
                            raise
                        log.debug("Old request message delete skipped: %s", e)

                try:
                    sent_city = await messenger.send(
                        chat_id=ctx.request_chat_id,
                        text=text_city,
                        reply_markup=city_markup,
                    )
                    sent_chat_id = int(sent_city.chat_id)
                    sent_message_id = int(sent_city.message_id)
                except MessengerError as e:
                    log.warning("Failed to send city request message: %r", e)
                    sent_chat_id = None
                    sent_message_id = None

            if sent_chat_id and sent_message_id and schedule_line:
                await self._sync_schedule_core(
                    req_id=src.req_id,
                    city=ctx.city,
                    hhmm=snapshot.hhmm,
                    line_text=schedule_line,
                    request_kind=parsed.kind,
                    client_name=ctx.chat_name,
                    request_chat_id=sent_chat_id,
                    request_message_id=sent_message_id,
                    sync_schedule_board=sync_schedule_board,
                )

                if old_city and old_city != ctx.city and sync_old_city_schedule:
                    await self.schedule_coordinator.sync(
                        old_city,
                        sync_board=sync_old_city_schedule,
                    )

        await replier.reply("✅ Заявка обновлена.")
        return EditCashRequestResult(
            ok=True,
            req_id=src.req_id,
            client_text=text_client,
            request_text=text_city,
            schedule_line=schedule_line,
        )
