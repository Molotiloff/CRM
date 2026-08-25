from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from services.accounting.fulfillment_models import (
    EnqueueFulfillment,
    FulfillmentRequestKind,
)
from services.act_counter import AppliedExchangeMovement
from services.crm.deal_service import DealCreateCommand
from services.unit_of_work import UnitOfWorkFactory

from .balance_service import ExchangeBalanceService


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


class ExchangeTransactionService:
    """Owns atomic ledger and exchange source-link mutations."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        balance_service: ExchangeBalanceService,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._balance_service = balance_service

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
        async with self._unit_of_work_factory() as unit_of_work:
            receive_sign, pay_sign = await self._balance_service.apply_cancel(
                client_id=command.client_id,
                chat_id=command.chat_id,
                message_id=command.card_message_id,
                req_id=command.request_id,
                recv_code=command.receive_code,
                recv_amount=command.receive_amount,
                pay_code=command.pay_code,
                pay_amount=command.pay_amount,
                recv_is_deposit=command.receive_is_deposit,
                pay_is_withdraw=command.pay_is_withdraw,
                tracked_currency_codes=_mutable_codes(command.tracked_currency_codes),
                unit_of_work=unit_of_work,
            )
            status_updated = await unit_of_work.exchange_requests.set_exchange_request_status(
                client_req_id=command.request_id,
                status="cancelled",
            )
            if not status_updated:
                await unit_of_work.exchange_requests.upsert_exchange_request_link(
                    client_req_id=command.request_id,
                    table_req_id=command.table_request_id,
                    status="cancelled",
                )
            await unit_of_work.commit()
        return CancelExchangeTransactionResult(receive_sign, pay_sign)


def _mutable_codes(codes: frozenset[str] | None) -> set[str] | None:
    return set(codes) if codes is not None else None
