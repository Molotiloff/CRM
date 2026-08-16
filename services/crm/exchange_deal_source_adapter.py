from __future__ import annotations

from dataclasses import dataclass

from domain import (
    Deal,
    ExchangeDealBody,
    ExchangeRequestSource,
    ExchangeRequestStatus,
    SourceKind,
)
from services.exchange.balance_service import ExchangeBalanceService
from services.exchange.card_parser import build_plain_card
from services.exchange.text_builder import ExchangeTextBuilder
from services.unit_of_work import UnitOfWorkPort

from .deal_service import DealUpdateCommand, DealValidationError
from .deal_source_adapter import DealSourceRepositoryPort
from .deal_source_commands import DealSourceEditCommand, ExchangeSourceEdit


@dataclass(frozen=True, slots=True)
class ExchangeDealSourceEditPlan:
    balance_service: ExchangeBalanceService
    deal: Deal
    command: ExchangeSourceEdit
    source: ExchangeRequestSource
    request_text: str
    update_command: DealUpdateCommand

    async def apply(self, unit_of_work: UnitOfWorkPort) -> None:
        message = self.source.primary_message
        receive = self.command.receive
        pay = self.command.pay
        await self.balance_service.apply_edit_delta(
            client_id=_required_int(self.deal.client_id, "deal client id"),
            old_request_text=_plain_card(self.source),
            recv_code_new=str(receive.currency),
            pay_code_new=str(pay.currency),
            recv_amount_new=receive.amount,
            pay_amount_new=pay.amount,
            recv_prec=receive.precision,
            pay_prec=pay.precision,
            chat_id=message.chat_id,
            target_bot_msg_id=message.message_id,
            cmd_msg_id=self.command.operation_id,
            recv_is_deposit=True,
            pay_is_withdraw=True,
            unit_of_work=unit_of_work,
        )
        await unit_of_work.exchange_requests.upsert_exchange_request_link(
            client_req_id=self.source.request_id,
            table_req_id=self.source.table_request_id,
            request_text=self.request_text,
            table_in_cur=str(receive.currency),
            table_out_cur=str(pay.currency),
            table_in_amount=receive.amount,
            table_out_amount=pay.amount,
            table_rate=format(self.command.rate.normalize(), "f"),
        )


class ExchangeDealSourceAdapter:
    source_kind = SourceKind.EXCHANGE

    def __init__(
        self,
        *,
        repository: DealSourceRepositoryPort,
        balance_service: ExchangeBalanceService,
    ) -> None:
        self._repository = repository
        self._balance = balance_service

    async def prepare_edit(
        self,
        deal: Deal,
        command: DealSourceEditCommand,
        *,
        actor_name: str,
    ) -> ExchangeDealSourceEditPlan:
        if not isinstance(command, ExchangeSourceEdit):
            raise DealValidationError("Invalid exchange source edit command")
        body = ExchangeDealBody.from_body(deal.body)
        req_id = deal.exchange_client_req_id or body.request_id
        source = await self._source(req_id)
        receive = command.receive
        pay = command.pay
        rate = format(command.rate.normalize(), "f")
        request_text = ExchangeTextBuilder.build_request_text(
            req_id=req_id,
            table_req_id=source.table_request_id,
            client_name=str(deal.client_name or body.client_name or "CRM client"),
            recv_code=str(receive.currency),
            recv_amount=receive.amount,
            recv_prec=receive.precision,
            pay_code=str(pay.currency),
            pay_amount=pay.amount,
            pay_prec=pay.precision,
            rate=rate,
            creator_name=actor_name,
            note=command.note,
        )
        updated_body = body.with_edit(
            receive=receive,
            pay=pay,
            rate=command.rate,
            note=command.note,
            operation_id=command.operation_id,
        )
        return ExchangeDealSourceEditPlan(
            balance_service=self._balance,
            deal=deal,
            command=command,
            source=source,
            request_text=request_text,
            update_command=DealUpdateCommand(
                body=updated_body,
                comment=command.note,
            ),
        )

    async def cancel(self, unit_of_work: UnitOfWorkPort, deal: Deal) -> None:
        body = ExchangeDealBody.from_body(deal.body)
        req_id = deal.exchange_client_req_id or body.request_id
        source = await self._source(req_id)
        message = source.primary_message
        await self._balance.apply_cancel(
            client_id=_required_int(deal.client_id, "deal client id"),
            chat_id=message.chat_id,
            message_id=message.message_id,
            req_id=req_id,
            recv_code=str(source.receive.currency),
            recv_amount=source.receive.amount,
            pay_code=str(source.pay.currency),
            pay_amount=source.pay.amount,
            recv_is_deposit=True,
            pay_is_withdraw=True,
            unit_of_work=unit_of_work,
        )
        updated = await unit_of_work.exchange_requests.set_exchange_request_status(
            client_req_id=req_id,
            status=ExchangeRequestStatus.CANCELLED,
        )
        if not updated:
            raise DealValidationError(f"Exchange request link {req_id} is not active")

    async def _source(self, request_id: str) -> ExchangeRequestSource:
        if not request_id:
            raise DealValidationError("Exchange request id is missing")
        source = await self._repository.get_exchange_source(request_id=request_id)
        if source is None:
            raise DealValidationError(
                f"Exchange request link {request_id} was not found"
            )
        return source


def _plain_card(source: ExchangeRequestSource) -> str:
    return build_plain_card(
        req_id=source.request_id,
        recv_code=str(source.receive.currency),
        recv_amount=source.receive.amount,
        pay_code=str(source.pay.currency),
        pay_amount=source.pay.amount,
        rate=source.rate,
    )


def _required_int(value: object, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise DealValidationError(f"Missing {field}") from None
