from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from domain import TelegramMessageRef
from observability import bind_log_context, logged_operation, measured_operation
from services.crm.telegram_deal_registrar import ExchangeDealData
from services.exchange.transaction_service import CreateExchangeTransaction
from services.exchange.use_case_base import _ExchangeUseCaseBase
from services.exchange.workflow_policy import is_request_chat, tracked_exchange_currencies
from services.messaging import MessengerError, MessengerPort, ReplierPort

log = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class CreateExchangeParams:
    """Транспортно-независимый вход создания заявки (C0.3).

    source_message_id — уникальный id команды-источника: из него собираются
    идемпотентные ключи денежных ног; не-Telegram вызыватель обязан дать
    уникальное число сам.
    """

    chat_id: int
    chat_name: str
    source_message_id: int
    recv_code: str
    recv_amount_expr: str
    pay_code: str
    pay_amount_expr: str
    creator_name: str = "unknown"
    recv_is_deposit: bool = True
    pay_is_withdraw: bool = True
    note: str | None = None
    reply_to_message_id: int | None = None  # None — карточка клиента без reply


@dataclass(slots=True, frozen=True)
class CreateExchangeResult:
    ok: bool
    req_id: str | None = None
    table_req_id: int | None = None
    error: str | None = None


class CreateExchangeRequest(_ExchangeUseCaseBase):
    @logged_operation("exchange.create")
    @measured_operation("exchange.create")
    async def execute_core(
        self,
        params: CreateExchangeParams,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
    ) -> CreateExchangeResult:
        chat_id = params.chat_id
        chat_name = params.chat_name

        try:
            client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
            accounts = await self.repo.snapshot_wallet(client_id)
            try:
                calc = self.calculator.calculate(
                    recv_code=params.recv_code,
                    recv_amount_expr=params.recv_amount_expr,
                    pay_code=params.pay_code,
                    pay_amount_expr=params.pay_amount_expr,
                    accounts=accounts,
                )
            except ValueError as e:
                await replier.reply(str(e))
                return CreateExchangeResult(ok=False, error=str(e))

            recv_code = calc.recv_code
            pay_code = calc.pay_code
            recv_amount = calc.recv_amount
            pay_amount = calc.pay_amount
            recv_prec = calc.recv_precision
            pay_prec = calc.pay_precision
            rate = calc.rate
            rate_text = calc.rate_text

            req_id = random.randint(10_000_000, 99_999_999)
            bind_log_context(request_id=req_id)
            table_req_id = await self.repo.next_request_id()

            texts = self.text_builder.build_new_texts(
                req_id=req_id,
                table_req_id=table_req_id,
                client_name=chat_name,
                recv_code=recv_code,
                recv_amount=recv_amount,
                recv_prec=recv_prec,
                pay_code=pay_code,
                pay_amount=pay_amount,
                pay_prec=pay_prec,
                rate=rate_text,
                creator_name=params.creator_name,
                note=params.note,
                formula=params.pay_amount_expr,
            )

            idem_recv = f"{chat_id}:{params.source_message_id}:recv"
            idem_pay = f"{chat_id}:{params.source_message_id}:pay"
            recv_comment = (
                params.recv_amount_expr
                if not params.note
                else f"{params.recv_amount_expr} | {params.note}"
            )
            pay_comment = (
                params.pay_amount_expr
                if not params.note
                else f"{params.pay_amount_expr} | {params.note}"
            )
            is_request_chat_origin = is_request_chat(chat_id, self.request_chat_id)
            deal_data = ExchangeDealData(
                source_ref=f"{chat_id}:{params.source_message_id}",
                client_id=client_id,
                client_req_id=str(req_id),
                table_req_id=int(table_req_id),
                client_name=chat_name,
                creator_name=params.creator_name,
                recv_code=recv_code,
                recv_amount=recv_amount,
                pay_code=pay_code,
                pay_amount=pay_amount,
                rate=rate,
                comment=params.note,
            )

            try:
                create_result = await self.transaction_service.create(
                    CreateExchangeTransaction(
                        request_id=str(req_id),
                        table_request_id=str(table_req_id),
                        client_id=client_id,
                        receive_code=recv_code,
                        receive_amount=recv_amount,
                        receive_comment=recv_comment,
                        pay_code=pay_code,
                        pay_amount=pay_amount,
                        pay_comment=pay_comment,
                        rate=rate,
                        receive_is_deposit=params.recv_is_deposit,
                        pay_is_withdraw=params.pay_is_withdraw,
                        receive_idempotency_key=idem_recv,
                        pay_idempotency_key=idem_pay,
                        tracked_currency_codes=tracked_exchange_currencies(
                            chat_id, self.request_chat_id
                        ),
                        deal_command=(
                            self.deal_registrar.build_exchange_command(deal_data)
                            if self.deal_registrar is not None
                            else None
                        ),
                    )
                )
            except Exception as leg_err:
                log.exception("Atomic create failed for exchange request %s", req_id)
                await replier.reply(f"Не удалось выполнить обмен: {leg_err}")
                return CreateExchangeResult(ok=False, error=str(leg_err))

            log.info(
                "Exchange request %s applied: %s %s -> %s %s (rate %s)",
                req_id,
                recv_amount,
                recv_code,
                pay_amount,
                pay_code,
                rate_text,
            )

            try:
                client_card_text = (
                    texts.request_text if is_request_chat_origin else texts.client_text
                )
                client_card_kb = (
                    self.keyboards.request_chat(
                        request_id=req_id,
                        table_request_id=table_req_id,
                    )
                    if is_request_chat_origin
                    else self.keyboards.client_cancel(
                        request_id=req_id,
                        table_request_id=table_req_id,
                    )
                )
                sent = await messenger.send(
                    chat_id,
                    client_card_text,
                    reply_markup=client_card_kb,
                    reply_to_message_id=params.reply_to_message_id,
                )
            except MessengerError as e:
                log.warning("Failed to send client card for exchange request %s: %r", req_id, e)
                sent = None

            if sent is not None:
                try:
                    await self.source_links.bind_client_card(
                        request_id=str(req_id),
                        table_request_id=str(table_req_id),
                        message=TelegramMessageRef(sent.chat_id, sent.message_id),
                        request_text=texts.request_text,
                        is_request_card=is_request_chat_origin,
                    )
                except Exception:
                    log.exception("Failed to persist exchange request client link %s", req_id)

                if is_request_chat_origin and self.act_counter_service:
                    try:
                        await self.act_counter_service.register_exchange_movements(
                            req_id=str(req_id),
                            table_req_id=str(table_req_id),
                            request_chat_id=int(sent.chat_id),
                            request_message_id=int(sent.message_id),
                            movements=list(create_result.movements),
                        )
                        await self._notify_act_current_amount(
                            messenger=messenger,
                            request_chat_id=int(sent.chat_id),
                        )
                    except Exception:
                        log.exception(
                            "Failed to update ACT for in-request-chat exchange request %s", req_id
                        )

            if self.request_chat_id and not is_request_chat_origin:
                try:
                    sent_request = await messenger.send(
                        self.request_chat_id,
                        texts.request_text,
                        reply_markup=self.keyboards.table_create(
                            receive_code=recv_code,
                            pay_code=pay_code,
                            receive_amount=recv_amount,
                            pay_amount=pay_amount,
                            rate=rate_text,
                            table_request_id=table_req_id,
                        ),
                    )
                    await self.source_links.bind_request_card(
                        request_id=str(req_id),
                        table_request_id=str(table_req_id),
                        message=TelegramMessageRef(sent_request.chat_id, sent_request.message_id),
                        request_text=texts.request_text,
                    )
                    if self.act_counter_service:
                        await self.act_counter_service.register_exchange_movements(
                            req_id=str(req_id),
                            table_req_id=str(table_req_id),
                            request_chat_id=int(sent_request.chat_id),
                            request_message_id=int(sent_request.message_id),
                            movements=list(create_result.movements),
                        )
                        if int(chat_id) != int(sent_request.chat_id):
                            await self.act_counter_service.apply_request_wallet_movements(
                                req_id=str(req_id),
                                table_req_id=str(table_req_id),
                                request_chat_id=int(sent_request.chat_id),
                                request_message_id=int(sent_request.message_id),
                                movements=list(create_result.movements),
                                chat_name=sent_request.chat_title,
                            )
                        await self._notify_act_current_amount(
                            messenger=messenger,
                            request_chat_id=int(sent_request.chat_id),
                        )
                except Exception:
                    log.exception("Failed to post or persist exchange request chat copy %s", req_id)

            if not is_request_chat_origin:
                accounts2 = await self.repo.snapshot_wallet(client_id)
                await replier.reply(
                    self.wallet_presenter.summary(chat_name, accounts2),
                    parse_mode="HTML",
                )

            return CreateExchangeResult(ok=True, req_id=str(req_id), table_req_id=int(table_req_id))

        except Exception as e:
            log.exception("Exchange request creation failed")
            await replier.reply(f"Не удалось выполнить операцию: {e}")
            return CreateExchangeResult(ok=False, error=str(e))
