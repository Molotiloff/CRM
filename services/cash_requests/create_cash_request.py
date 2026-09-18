from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from observability import bind_log_context, logged_operation, measured_operation
from services.cash_requests.audit import RequestAudit, audit_lines_for_request_chat
from services.cash_requests.calculator import CashRequestCalculationError
from services.cash_requests.parsing import ParsedRequest
from services.cash_requests.request_use_case_base import (
    CashRequestUseCaseBase,
    ScheduleBoardSync,
)
from services.cash_requests.workflow_models import CashRequestCommand, CashRequestResult
from services.crm.telegram_deal_registrar import CashDealData
from services.messaging import MessengerError, MessengerPort, ReplierPort

log = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class CreateCashRequestParams(CashRequestCommand):
    chat_name: str
    parsed: ParsedRequest
    creator_name: str = "unknown"
    source_message_id: int | None = None
    client_group: str | None = None


CreateCashRequestResult = CashRequestResult


class CreateCashRequest(CashRequestUseCaseBase):
    @logged_operation("cash_request.create")
    @measured_operation("cash_request.create")
    async def execute_core(
        self,
        params: CreateCashRequestParams,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
        sync_schedule_board: ScheduleBoardSync | None = None,
    ) -> CreateCashRequestResult:
        parsed = params.parsed
        audit = RequestAudit(created_by=params.creator_name, changed_by=None, changed_ts=None)
        ctx = await self._build_request_context_core(
            chat_id=params.chat_id,
            chat_name=params.chat_name,
            city=parsed.city,
            client_group=params.client_group,
        )
        accounts = await self.repo.snapshot_wallet(ctx.client_id)

        try:
            details = self.calculator.calculate(parsed, accounts)
        except CashRequestCalculationError as exc:
            await replier.reply(str(exc))
            return CreateCashRequestResult(ok=False, error=str(exc))

        req_id = self._gen_req_id()
        bind_log_context(request_id=req_id)
        pin_code = self._gen_pin()
        plan = self.card_presenter.build(
            details=details,
            request_id=req_id,
            city=ctx.city,
            client_name=ctx.chat_name,
            pin_code=pin_code,
            contact1=parsed.contact1,
            contact2=parsed.contact2,
            comment=parsed.comment,
            audit_lines=audit_lines_for_request_chat(audit),
            changed=False,
        )
        text_client = plan.client_text
        text_city = plan.request_text
        city_markup = plan.request_markup
        schedule_line = plan.schedule_line

        sent_client = await replier.reply(text_client, parse_mode="HTML", reply_markup=None)

        sent_city = None
        if ctx.request_chat_id:
            try:
                sent_city = await messenger.send(
                    chat_id=ctx.request_chat_id,
                    text=text_city,
                    reply_markup=city_markup,
                )
            except MessengerError as e:
                log.warning("Failed to send city request message: %r", e)
                sent_city = None

            if sent_city and schedule_line:
                await self._sync_schedule_core(
                    req_id=req_id,
                    city=ctx.city,
                    line_text=schedule_line,
                    request_kind=parsed.kind,
                    client_name=ctx.chat_name,
                    request_chat_id=sent_city.chat_id,
                    request_message_id=sent_city.message_id,
                    sync_schedule_board=sync_schedule_board,
                )

        if self.deal_registrar is not None:
            try:
                source_message_id = params.source_message_id or req_id
                await self.deal_registrar.register_cash(
                    CashDealData(
                        source_ref=f"{params.chat_id}:{source_message_id}",
                        client_id=ctx.client_id,
                        req_id=req_id,
                        city=ctx.city,
                        client_name=ctx.chat_name,
                        creator_name=params.creator_name,
                        kind=parsed.kind,
                        comment=parsed.comment or None,
                        code=(str(details.money.currency) if details.money else None),
                        amount=(
                            details.money.amount.quantize(Decimal("1")) if details.money else None
                        ),
                        in_code=(str(details.receive.currency) if details.receive else None),
                        in_amount=(
                            details.receive.amount.quantize(Decimal("1"))
                            if details.receive
                            else None
                        ),
                        out_code=(str(details.pay.currency) if details.pay else None),
                        out_amount=(
                            details.pay.amount.quantize(Decimal("1")) if details.pay else None
                        ),
                        client_text=text_client,
                        request_text=text_city,
                        client_chat_id=(sent_client.chat_id if sent_client else params.chat_id),
                        client_message_id=(sent_client.message_id if sent_client else None),
                    )
                )
            except Exception:
                log.exception("Failed to mirror cash request %s to CRM", req_id)

        return CreateCashRequestResult(ok=True, req_id=req_id)
