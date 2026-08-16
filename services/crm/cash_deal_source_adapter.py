from __future__ import annotations

from dataclasses import dataclass

from domain import CashDealBody, CashRequestKind, Deal, ScheduleEntry, SourceKind
from services.cash_requests.edit_cash_request import EditCashRequest, EditCashRequestParams
from services.cash_requests.parsing import ParsedRequest
from services.messaging import CollectingReplier
from services.unit_of_work import UnitOfWorkPort

from .deal_service import DealUpdateCommand, DealValidationError
from .deal_source_adapter import DealSourceRepositoryPort
from .deal_source_commands import CashSourceEdit, DealSourceEditCommand


@dataclass(frozen=True, slots=True)
class CashDealSourceEditPlan:
    entry: ScheduleEntry
    schedule_line: str
    update_command: DealUpdateCommand

    async def apply(self, unit_of_work: UnitOfWorkPort) -> None:
        await unit_of_work.request_schedule.upsert_request_schedule_entry(
            req_id=self.entry.request_id,
            city=str(self.entry.city),
            hhmm=self.entry.hhmm,
            request_kind=self.entry.kind,
            line_text=self.schedule_line,
            client_name=self.entry.client_name,
            request_chat_id=self.entry.request_message.chat_id,
            request_message_id=self.entry.request_message.message_id,
        )


class CashDealSourceAdapter:
    source_kind = SourceKind.CASH

    def __init__(
        self,
        *,
        repository: DealSourceRepositoryPort,
        cash_edit: EditCashRequest,
    ) -> None:
        self._repository = repository
        self._cash_edit = cash_edit

    async def prepare_edit(
        self,
        deal: Deal,
        command: DealSourceEditCommand,
        *,
        actor_name: str,
    ) -> CashDealSourceEditPlan:
        if not isinstance(command, CashSourceEdit):
            raise DealValidationError("Invalid cash source edit command")
        body = CashDealBody.from_body(deal.body)
        entry = await self._entry(body.request_id)
        self._validate_entry(body, entry, command)
        message = body.require_client_message()
        prepared = await self._cash_edit.prepare_core(
            EditCashRequestParams(
                chat_id=message.chat_id,
                chat_name=deal.client_name or body.client_name or "CRM client",
                parsed=_parsed_request(body, command, entry.kind),
                old_text=body.base_text,
                reply_msg_id=message.message_id,
                editor_name=actor_name,
            ),
            replier=CollectingReplier(),
        )
        if not prepared.ok:
            raise DealValidationError(prepared.error or "Cash request edit failed")

        updated_body = _updated_body(
            body,
            command,
            kind=entry.kind,
            client_text=prepared.client_text,
            request_text=prepared.request_text,
        )
        return CashDealSourceEditPlan(
            entry=entry,
            schedule_line=str(prepared.schedule_line or entry.line_text),
            update_command=DealUpdateCommand(
                body=updated_body,
                comment=command.comment,
            ),
        )

    async def cancel(self, unit_of_work: UnitOfWorkPort, deal: Deal) -> None:
        body = CashDealBody.from_body(deal.body)
        entry = await self._entry(body.request_id)
        if body.kind is not entry.kind:
            raise DealValidationError(
                "Cash request kind does not match its schedule entry"
            )
        removed = await unit_of_work.request_schedule.deactivate_request_schedule_entry(
            req_id=entry.request_id
        )
        if not removed:
            raise DealValidationError(
                f"Cash request schedule {entry.request_id} is not active"
            )

    async def _entry(self, request_id: str) -> ScheduleEntry:
        if not request_id:
            raise DealValidationError("Cash request id is missing")
        entry = await self._repository.get_schedule_entry(request_id=request_id)
        if entry is None:
            raise DealValidationError(
                f"Cash request schedule {request_id} was not found"
            )
        return entry

    @staticmethod
    def _validate_entry(
        body: CashDealBody,
        entry: ScheduleEntry,
        command: CashSourceEdit,
    ) -> None:
        if body.kind is not entry.kind:
            raise DealValidationError(
                "Cash request kind does not match its schedule entry"
            )
        if command.city != entry.city:
            raise DealValidationError(
                "Changing cash request city from CRM is not supported yet"
            )


def _parsed_request(
    body: CashDealBody,
    command: CashSourceEdit,
    kind: CashRequestKind,
) -> ParsedRequest:
    if kind in {CashRequestKind.DEPOSIT, CashRequestKind.WITHDRAWAL}:
        if command.amount is None:
            raise DealValidationError("Cash amount must be greater than zero")
        return ParsedRequest(
            cmd="crm",
            kind=kind,
            city=str(command.city),
            amount_expr=str(command.amount),
            code=str(body.money.currency) if body.money is not None else "",
            contact1=command.contact1,
            contact2=command.contact2,
            comment=command.comment or "",
        )
    if kind is CashRequestKind.EXCHANGE:
        if command.in_amount is None or command.out_amount is None:
            raise DealValidationError("Cash FX amounts are required")
        return ParsedRequest(
            cmd="crm",
            kind=kind,
            city=str(command.city),
            in_code=str(body.receive.currency) if body.receive is not None else "",
            out_code=str(body.pay.currency) if body.pay is not None else "",
            amt_in_expr=str(command.in_amount),
            amt_out_expr=str(command.out_amount),
            contact1=command.contact1,
            contact2=command.contact2,
            comment=command.comment or "",
        )
    raise DealValidationError(f"Unsupported cash request kind: {kind}")


def _updated_body(
    body: CashDealBody,
    command: CashSourceEdit,
    *,
    kind: CashRequestKind,
    client_text: str | None,
    request_text: str | None,
) -> CashDealBody:
    if kind in {CashRequestKind.DEPOSIT, CashRequestKind.WITHDRAWAL}:
        if command.amount is None:
            raise DealValidationError("Cash amount must be greater than zero")
        return body.with_single_amount(
            command.amount,
            client_text=client_text,
            request_text=request_text,
            note=command.comment,
        )
    if command.in_amount is None or command.out_amount is None:
        raise DealValidationError("Cash FX amounts are required")
    return body.with_exchange_amounts(
        command.in_amount,
        command.out_amount,
        client_text=client_text,
        request_text=request_text,
        note=command.comment,
    )
