from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from db_asyncpg.ports.ledger import TransactionRepositoryPort
from services.act_counter import AppliedExchangeMovement
from services.exchange.card_parser import parse_get_give
from services.exchange.ledger import (
    apply_ledger_commands,
    plan_cancel_ledger,
    plan_create_ledger,
    plan_edit_ledger,
)
from services.unit_of_work import UnitOfWorkFactory, UnitOfWorkPort


@dataclass(slots=True, frozen=True)
class CreateExchangeBalanceResult:
    movements: list[AppliedExchangeMovement]


class ExchangeBalanceService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def apply_create(
        self,
        *,
        client_id: int,
        recv_code: str,
        recv_amount: Decimal,
        recv_comment: str,
        pay_code: str,
        pay_amount: Decimal,
        pay_comment: str,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
        idem_recv: str,
        idem_pay: str,
        tracked_currency_codes: set[str] | None = None,
        unit_of_work: UnitOfWorkPort | None = None,
    ) -> CreateExchangeBalanceResult:
        if unit_of_work is not None:
            return await self._apply_create(
                unit_of_work.transactions,
                client_id=client_id,
                recv_code=recv_code,
                recv_amount=recv_amount,
                recv_comment=recv_comment,
                pay_code=pay_code,
                pay_amount=pay_amount,
                pay_comment=pay_comment,
                recv_is_deposit=recv_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
                idem_recv=idem_recv,
                idem_pay=idem_pay,
                tracked_currency_codes=tracked_currency_codes,
            )
        async with self._unit_of_work_factory() as uow:
            result = await self._apply_create(
                uow.transactions,
                client_id=client_id,
                recv_code=recv_code,
                recv_amount=recv_amount,
                recv_comment=recv_comment,
                pay_code=pay_code,
                pay_amount=pay_amount,
                pay_comment=pay_comment,
                recv_is_deposit=recv_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
                idem_recv=idem_recv,
                idem_pay=idem_pay,
                tracked_currency_codes=tracked_currency_codes,
            )
            await uow.commit()
            return result

    async def _apply_create(
        self,
        repo: TransactionRepositoryPort,
        *,
        client_id: int,
        recv_code: str,
        recv_amount: Decimal,
        recv_comment: str,
        pay_code: str,
        pay_amount: Decimal,
        pay_comment: str,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
        idem_recv: str,
        idem_pay: str,
        tracked_currency_codes: set[str] | None,
    ) -> CreateExchangeBalanceResult:
        commands = plan_create_ledger(
            receive_code=recv_code,
            receive_amount=recv_amount,
            receive_comment=recv_comment,
            pay_code=pay_code,
            pay_amount=pay_amount,
            pay_comment=pay_comment,
            receive_is_deposit=recv_is_deposit,
            pay_is_withdraw=pay_is_withdraw,
            receive_idempotency_key=idem_recv,
            pay_idempotency_key=idem_pay,
            tracked_currency_codes=tracked_currency_codes,
        )
        movements = await apply_ledger_commands(repo, client_id=client_id, commands=commands)
        return CreateExchangeBalanceResult(movements=movements)

    async def apply_edit_delta(
        self,
        *,
        client_id: int,
        old_request_text: str,
        recv_code_new: str,
        pay_code_new: str,
        recv_amount_new: Decimal,
        pay_amount_new: Decimal,
        recv_prec: int,
        pay_prec: int,
        chat_id: int,
        target_bot_msg_id: int,
        cmd_msg_id: int,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
        tracked_currency_codes: set[str] | None = None,
        unit_of_work: UnitOfWorkPort | None = None,
    ) -> list[AppliedExchangeMovement]:
        if unit_of_work is not None:
            return await self._apply_edit_delta(
                unit_of_work.transactions,
                client_id=client_id,
                old_request_text=old_request_text,
                recv_code_new=recv_code_new,
                pay_code_new=pay_code_new,
                recv_amount_new=recv_amount_new,
                pay_amount_new=pay_amount_new,
                recv_prec=recv_prec,
                pay_prec=pay_prec,
                chat_id=chat_id,
                target_bot_msg_id=target_bot_msg_id,
                cmd_msg_id=cmd_msg_id,
                recv_is_deposit=recv_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
                tracked_currency_codes=tracked_currency_codes,
            )
        async with self._unit_of_work_factory() as uow:
            movements = await self._apply_edit_delta(
                uow.transactions,
                client_id=client_id,
                old_request_text=old_request_text,
                recv_code_new=recv_code_new,
                pay_code_new=pay_code_new,
                recv_amount_new=recv_amount_new,
                pay_amount_new=pay_amount_new,
                recv_prec=recv_prec,
                pay_prec=pay_prec,
                chat_id=chat_id,
                target_bot_msg_id=target_bot_msg_id,
                cmd_msg_id=cmd_msg_id,
                recv_is_deposit=recv_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
                tracked_currency_codes=tracked_currency_codes,
            )
            await uow.commit()
            return movements

    async def _apply_edit_delta(
        self,
        repo: TransactionRepositoryPort,
        *,
        client_id: int,
        old_request_text: str,
        recv_code_new: str,
        pay_code_new: str,
        recv_amount_new: Decimal,
        pay_amount_new: Decimal,
        recv_prec: int,
        pay_prec: int,
        chat_id: int,
        target_bot_msg_id: int,
        cmd_msg_id: int,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
        tracked_currency_codes: set[str] | None,
    ) -> list[AppliedExchangeMovement]:
        parsed = parse_get_give(old_request_text)
        if not parsed:
            return []

        (old_recv_amt_raw, old_recv_code), (old_pay_amt_raw, old_pay_code) = parsed
        q_recv = Decimal(10) ** -recv_prec
        q_pay = Decimal(10) ** -pay_prec
        old_recv_amt = old_recv_amt_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
        old_pay_amt = old_pay_amt_raw.quantize(q_pay, rounding=ROUND_HALF_UP)
        commands = plan_edit_ledger(
            old_receive_code=old_recv_code,
            old_receive_amount=old_recv_amt,
            old_pay_code=old_pay_code,
            old_pay_amount=old_pay_amt,
            receive_code=recv_code_new,
            receive_amount=recv_amount_new,
            pay_code=pay_code_new,
            pay_amount=pay_amount_new,
            receive_is_deposit=recv_is_deposit,
            pay_is_withdraw=pay_is_withdraw,
            idempotency_prefix=f"edit:{chat_id}:{target_bot_msg_id}:{cmd_msg_id}",
            tracked_currency_codes=tracked_currency_codes,
        )
        return await apply_ledger_commands(repo, client_id=client_id, commands=commands)

    async def apply_cancel(
        self,
        *,
        client_id: int,
        chat_id: int,
        message_id: int,
        req_id: str,
        recv_code: str,
        recv_amount: Decimal,
        pay_code: str,
        pay_amount: Decimal,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
        tracked_currency_codes: set[str] | None = None,
        unit_of_work: UnitOfWorkPort | None = None,
    ) -> tuple[str | None, str | None]:
        if unit_of_work is not None:
            return await self._apply_cancel(
                unit_of_work.transactions,
                client_id=client_id,
                chat_id=chat_id,
                message_id=message_id,
                req_id=req_id,
                recv_code=recv_code,
                recv_amount=recv_amount,
                pay_code=pay_code,
                pay_amount=pay_amount,
                recv_is_deposit=recv_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
                tracked_currency_codes=tracked_currency_codes,
            )
        async with self._unit_of_work_factory() as uow:
            signs = await self._apply_cancel(
                uow.transactions,
                client_id=client_id,
                chat_id=chat_id,
                message_id=message_id,
                req_id=req_id,
                recv_code=recv_code,
                recv_amount=recv_amount,
                pay_code=pay_code,
                pay_amount=pay_amount,
                recv_is_deposit=recv_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
                tracked_currency_codes=tracked_currency_codes,
            )
            await uow.commit()
            return signs

    async def _apply_cancel(
        self,
        repo: TransactionRepositoryPort,
        *,
        client_id: int,
        chat_id: int,
        message_id: int,
        req_id: str,
        recv_code: str,
        recv_amount: Decimal,
        pay_code: str,
        pay_amount: Decimal,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
        tracked_currency_codes: set[str] | None,
    ) -> tuple[str | None, str | None]:
        commands, receive_sign, pay_sign = plan_cancel_ledger(
            request_id=req_id,
            chat_id=chat_id,
            message_id=message_id,
            receive_code=recv_code,
            receive_amount=recv_amount,
            pay_code=pay_code,
            pay_amount=pay_amount,
            receive_is_deposit=recv_is_deposit,
            pay_is_withdraw=pay_is_withdraw,
            tracked_currency_codes=tracked_currency_codes,
        )
        await apply_ledger_commands(repo, client_id=client_id, commands=commands)
        return receive_sign, pay_sign
