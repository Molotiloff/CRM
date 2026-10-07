from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from domain import DealStatus, DomainStateError
from services.accounting.fulfillment_models import (
    EnqueueFulfillment,
    FulfillmentRequestKind,
)
from services.act_counter import AppliedExchangeMovement
from services.crm.deal_service import DealCreateCommand, DealService, DealStatusCommand
from services.unit_of_work import UnitOfWorkFactory

from .balance_service import ExchangeBalanceService
from .workflow_policy import tracked_exchange_currencies

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CreateExchangeTransaction:
    request_id: str
    table_request_id: str
    client_id: int
    receive_code: str
    receive_amount: Decimal
    receive_comment: str
    pay_code: str
    pay_amount: Decimal
    pay_comment: str
    rate: Decimal
    receive_is_deposit: bool
    pay_is_withdraw: bool
    receive_idempotency_key: str
    pay_idempotency_key: str
    tracked_currency_codes: frozenset[str] | None = None
    deal_command: DealCreateCommand | None = None


@dataclass(frozen=True, slots=True)
class EditExchangeTransaction:
    request_id: str
    table_request_id: str
    client_id: int
    old_card_text: str
    receive_code: str
    receive_amount: Decimal
    receive_precision: int
    pay_code: str
    pay_amount: Decimal
    pay_precision: int
    rate: Decimal
    chat_id: int
    card_message_id: int
    command_message_id: int
    receive_is_deposit: bool
    pay_is_withdraw: bool
    tracked_currency_codes: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class CancelExchangeTransaction:
    request_id: str
    table_request_id: str
    client_id: int
    receive_code: str
    receive_amount: Decimal
    pay_code: str
    pay_amount: Decimal
    chat_id: int
    card_message_id: int
    receive_is_deposit: bool
    pay_is_withdraw: bool
    tracked_currency_codes: frozenset[str] | None = None
    request_chat_id: int | None = None


@dataclass(frozen=True, slots=True)
class CreateExchangeTransactionResult:
    movements: tuple[AppliedExchangeMovement, ...]


@dataclass(frozen=True, slots=True)
class EditExchangeTransactionResult:
    movements: tuple[AppliedExchangeMovement, ...]


@dataclass(frozen=True, slots=True)
class CancelExchangeTransactionResult:
    receive_operation_sign: str | None
    pay_operation_sign: str | None
    client_id: int


class ExchangeTransactionService:
    """Owns atomic ledger and exchange source-link mutations."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        balance_service: ExchangeBalanceService,
        deal_service: DealService | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._balance_service = balance_service
        self._deal_service = deal_service

    async def create(self, command: CreateExchangeTransaction) -> CreateExchangeTransactionResult:
        async with self._unit_of_work_factory() as unit_of_work:
            balance_result = await self._balance_service.apply_create(
                client_id=command.client_id,
                recv_code=command.receive_code,
                recv_amount=command.receive_amount,
                recv_comment=command.receive_comment,
                pay_code=command.pay_code,
                pay_amount=command.pay_amount,
                pay_comment=command.pay_comment,
                recv_is_deposit=command.receive_is_deposit,
                pay_is_withdraw=command.pay_is_withdraw,
                idem_recv=command.receive_idempotency_key,
                idem_pay=command.pay_idempotency_key,
                tracked_currency_codes=_mutable_codes(command.tracked_currency_codes),
                unit_of_work=unit_of_work,
            )
            await unit_of_work.exchange_requests.upsert_exchange_request_link(
                client_req_id=command.request_id,
                table_req_id=command.table_request_id,
                table_in_cur=command.receive_code,
                table_out_cur=command.pay_code,
                table_in_amount=command.receive_amount,
                table_out_amount=command.pay_amount,
                table_rate=command.rate,
                status="active",
            )
            if command.deal_command is not None:
                deal, _ = await unit_of_work.deals.create_deal_idempotent(
                    command.deal_command
                )
                if str(command.deal_command.deal_type) == "sale" and (
                    command.pay_code.upper() == "USDT"
                ):
                    await unit_of_work.fulfillment_queue.enqueue(
                        EnqueueFulfillment(
                            deal_id=deal.id,
                            request_kind=FulfillmentRequestKind.SALE,
                            qty=command.pay_amount,
                            actor_user_id=command.deal_command.actor_user_id,
                        )
                    )
            await unit_of_work.commit()
        return CreateExchangeTransactionResult(tuple(balance_result.movements))

    async def edit(self, command: EditExchangeTransaction) -> EditExchangeTransactionResult:
        async with self._unit_of_work_factory() as unit_of_work:
            deal = await unit_of_work.deals.get_by_exchange_request_id(command.request_id)
            if deal is not None and (deal.body.get("referrer_spread_payment") or {}).get("status") == "paid":
                raise DomainStateError("Нельзя менять заявку после выплаты спреда КТ; отмените сделку")
            movements = await self._balance_service.apply_edit_delta(
                client_id=command.client_id,
                old_request_text=command.old_card_text,
                recv_code_new=command.receive_code,
                pay_code_new=command.pay_code,
                recv_amount_new=command.receive_amount,
                pay_amount_new=command.pay_amount,
                recv_prec=command.receive_precision,
                pay_prec=command.pay_precision,
                chat_id=command.chat_id,
                target_bot_msg_id=command.card_message_id,
                cmd_msg_id=command.command_message_id,
                recv_is_deposit=command.receive_is_deposit,
                pay_is_withdraw=command.pay_is_withdraw,
                tracked_currency_codes=_mutable_codes(command.tracked_currency_codes),
                unit_of_work=unit_of_work,
            )
            await unit_of_work.exchange_requests.upsert_exchange_request_link(
                client_req_id=command.request_id,
                table_req_id=command.table_request_id,
                table_in_cur=command.receive_code,
                table_out_cur=command.pay_code,
                table_in_amount=command.receive_amount,
                table_out_amount=command.pay_amount,
                table_rate=command.rate,
            )
            await unit_of_work.commit()
        return EditExchangeTransactionResult(tuple(movements))

    async def cancel(self, command: CancelExchangeTransaction) -> CancelExchangeTransactionResult:
        updated_deal = None
        async with self._unit_of_work_factory() as unit_of_work:
            deal = await unit_of_work.deals.get_by_exchange_request_id(command.request_id)
            if deal is not None and deal.status in {DealStatus.DONE, DealStatus.CANCELED}:
                raise DomainStateError(f"Сделка уже имеет статус {deal.status}")
            if not await unit_of_work.exchange_requests.claim_exchange_request_cancellation(
                client_req_id=command.request_id
            ):
                raise DomainStateError("Заявка уже отменена или связь с ней не найдена")
            client_id = int(deal.client_id) if deal is not None and deal.client_id else command.client_id
            receive_is_deposit = command.receive_is_deposit
            pay_is_withdraw = command.pay_is_withdraw
            tracked_codes = command.tracked_currency_codes
            if deal is not None:
                body = deal.body.to_dict()
                if isinstance(body.get("recv_is_deposit"), bool):
                    receive_is_deposit = body["recv_is_deposit"]
                    pay_is_withdraw = body["pay_is_withdraw"]
                elif str(deal.source) == "crm":
                    receive_is_deposit = pay_is_withdraw = True
                else:
                    try:
                        origin_chat_id = int(str(deal.source_ref).split(":", 1)[0])
                    except (TypeError, ValueError):
                        origin_chat_id = command.chat_id
                    origin_is_request = (
                        command.request_chat_id is not None
                        and origin_chat_id == command.request_chat_id
                    )
                    receive_is_deposit = pay_is_withdraw = origin_is_request
                if str(deal.source) != "crm":
                    try:
                        origin_chat_id = int(str(deal.source_ref).split(":", 1)[0])
                        tracked_codes = tracked_exchange_currencies(
                            origin_chat_id, command.request_chat_id
                        )
                    except (TypeError, ValueError):
                        pass
                else:
                    tracked_codes = None
            receive_sign, pay_sign = await self._balance_service.apply_cancel(
                client_id=client_id,
                chat_id=command.chat_id,
                message_id=command.card_message_id,
                req_id=command.request_id,
                recv_code=command.receive_code,
                recv_amount=command.receive_amount,
                pay_code=command.pay_code,
                pay_amount=command.pay_amount,
                recv_is_deposit=receive_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
                tracked_currency_codes=_mutable_codes(tracked_codes),
                unit_of_work=unit_of_work,
            )
            if deal is not None:
                changed = await unit_of_work.deals.change_status(
                    deal.id,
                    DealStatusCommand(
                        status=DealStatus.CANCELED,
                        expected_old_status=deal.status,
                        actor_user_id=None,
                        payload={"reason": "exchange_cancelled_in_telegram"},
                    ),
                )
                if changed is None:
                    raise DomainStateError(f"CRM deal for request {command.request_id} disappeared")
                updated_deal = changed[0]
            await unit_of_work.commit()
        if updated_deal is not None and self._deal_service is not None:
            try:
                await self._deal_service.publish_committed_status_change(
                    updated_deal, new_status=DealStatus.CANCELED,
                    payload={"reason": "exchange_cancelled_in_telegram"},
                )
            except Exception:
                log.exception("Failed to publish canceled exchange %s", command.request_id)
        return CancelExchangeTransactionResult(receive_sign, pay_sign, client_id)


def _mutable_codes(codes: frozenset[str] | None) -> set[str] | None:
    return set(codes) if codes is not None else None
