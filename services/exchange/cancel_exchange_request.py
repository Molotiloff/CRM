from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from domain import TelegramMessageRef
from observability import bind_log_context, logged_operation, measured_operation
from services.exchange.card_parser import parse_get_give
from services.exchange.notification_builder import CancelledBalanceLeg
from services.exchange.transaction_service import CancelExchangeTransaction
from services.exchange.use_case_base import _ExchangeUseCaseBase
from services.exchange.workflow_policy import is_request_chat, tracked_exchange_currencies
from services.messaging import MessengerError, MessengerPort, ReplierPort

log = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class CancelExchangeParams:
    """Транспортно-независимый вход отмены заявки (C0.3).

    card_text — текст карточки заявки: из него парсятся суммы (как и раньше);
    не-Telegram вызыватель берёт его из exchange_request_links.request_text.
    card_message_id — id карточки: правка карточки + идемпотентные ключи отмены.
    """

    chat_id: int
    chat_name: str
    card_message_id: int
    card_text: str
    req_id: str
    table_req_id_hint: str | None = None
    recv_is_deposit: bool = True
    pay_is_withdraw: bool = True


@dataclass(slots=True, frozen=True)
class CancelExchangeResult:
    ok: bool
    error: str | None = None


class CancelExchangeRequest(_ExchangeUseCaseBase):
    @logged_operation("exchange.cancel")
    @measured_operation("exchange.cancel")
    async def execute_core(
        self,
        params: CancelExchangeParams,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
    ) -> CancelExchangeResult:
        req_id_s = params.req_id
        bind_log_context(request_id=req_id_s)
        chat_id = params.chat_id

        parsed_amounts = parse_get_give(params.card_text)
        if not parsed_amounts:
            await replier.alert("Не удалось распознать заявку")
            return CancelExchangeResult(ok=False, error="card text not parsed")

        (recv_amt_raw, recv_code), (pay_amt_raw, pay_code) = parsed_amounts

        client_id = await self.repo.ensure_client(chat_id=chat_id, name=params.chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)
        single_request_chat_card = is_request_chat(chat_id, self.request_chat_id)

        def find_account(code: str):
            return next(
                (row for row in accounts if str(row["currency_code"]).upper() == code.upper()),
                None,
            )

        acc_recv = find_account(recv_code)
        acc_pay = find_account(pay_code)
        if not acc_recv or not acc_pay:
            await replier.alert("Счёта клиента изменились. Проверьте /кошелек")
            return CancelExchangeResult(ok=False, error="account missing")

        recv_prec = int(acc_recv["precision"])
        pay_prec = int(acc_pay["precision"])
        recv_amt = recv_amt_raw.quantize(Decimal(10) ** -recv_prec, rounding=ROUND_HALF_UP)
        pay_amt = pay_amt_raw.quantize(Decimal(10) ** -pay_prec, rounding=ROUND_HALF_UP)
        meta = await self._get_exchange_request_meta(req_id_s)
        table_req_id = params.table_req_id_hint or (meta.table_request_id if meta else None)

        try:
            transaction_result = await self.transaction_service.cancel(
                CancelExchangeTransaction(
                    request_id=req_id_s,
                    table_request_id=str(table_req_id or req_id_s),
                    client_id=client_id,
                    chat_id=chat_id,
                    card_message_id=params.card_message_id,
                    receive_code=recv_code,
                    receive_amount=recv_amt,
                    pay_code=pay_code,
                    pay_amount=pay_amt,
                    receive_is_deposit=params.recv_is_deposit,
                    pay_is_withdraw=params.pay_is_withdraw,
                    tracked_currency_codes=tracked_exchange_currencies(
                        chat_id, self.request_chat_id
                    ),
                )
            )
            recv_op_sign = transaction_result.receive_operation_sign
            pay_op_sign = transaction_result.pay_operation_sign
        except Exception as e:
            log.exception("Atomic cancel failed for exchange request %s", req_id_s)
            await replier.alert(f"Не удалось отменить: {e}")
            return CancelExchangeResult(ok=False, error=str(e))

        log.info("Exchange request %s cancelled", req_id_s)

        request_copy = None
        if meta and meta.request_message:
            request_copy = (
                meta.request_message.chat_id,
                meta.request_message.message_id,
            )
        request_text = meta.request_text if meta else None
        single_request_chat_card = bool(
            request_copy is not None
            and int(chat_id) == int(request_copy[0])
            and int(params.card_message_id) == int(request_copy[1])
        )

        if not single_request_chat_card:
            try:
                await messenger.edit_text(
                    chat_id,
                    params.card_message_id,
                    self.notification_builder.cancelled_client_card(
                        params.card_text, cancelled_at=datetime.now()
                    ),
                    reply_markup=None,
                )
            except MessengerError as e:
                # Не удалось переписать текст (например, новый текст не прошёл
                # валидацию Telegram) — хотя бы снимем клавиатуру.
                log.debug("Cancel annotation edit_text failed (%s); stripping keyboard", e)
                try:
                    await messenger.edit_reply_markup(chat_id, params.card_message_id, None)
                except MessengerError as strip_err:
                    if not strip_err.benign:
                        raise
                    log.debug("Ignored benign edit error (cancel: strip keyboard): %s", strip_err)

        if request_copy is not None and request_text:
            try:
                cancelled_text = self.notification_builder.cancelled_request_card(request_text)
                if single_request_chat_card:
                    await messenger.edit_text(
                        chat_id, params.card_message_id, cancelled_text, reply_markup=None
                    )
                else:
                    await messenger.edit_text(
                        request_copy[0], request_copy[1], cancelled_text, reply_markup=None
                    )
                await self.source_links.mark_request_card_cancelled(
                    request_id=str(req_id_s),
                    table_request_id=str(table_req_id or req_id_s),
                    message=TelegramMessageRef(request_copy[0], request_copy[1]),
                    request_text=cancelled_text,
                )
            except Exception:
                log.exception(
                    "Failed to mark request chat copy cancelled for exchange request %s", req_id_s
                )

        if self.act_counter_service:
            try:
                act_chat_id = None
                if request_copy is not None:
                    act_chat_id = int(request_copy[0])
                elif meta and meta.request_message:
                    act_chat_id = meta.request_message.chat_id
                if act_chat_id is not None and int(chat_id) != int(act_chat_id):
                    await self.act_counter_service.revert_request_wallet_movements(
                        req_id=str(req_id_s),
                        request_chat_id=act_chat_id,
                    )
                await self.act_counter_service.cancel_request(req_id=str(req_id_s))
                if act_chat_id is not None:
                    await self._notify_act_current_amount(
                        messenger=messenger,
                        request_chat_id=act_chat_id,
                    )
            except Exception:
                log.exception("Failed to cancel ACT movements for exchange request %s", req_id_s)

        table_done = bool(
            meta and table_req_id and meta.table_request_id == str(table_req_id) and meta.table_done
        )

        if self.request_chat_id and table_req_id and table_done:
            try:
                await messenger.send(
                    self.request_chat_id,
                    self.notification_builder.table_delete_prompt(req_id_s, table_req_id),
                    reply_markup=self.keyboards.table_delete(table_request_id=table_req_id),
                )
            except Exception:
                log.exception(
                    "Failed to post table delete prompt for exchange request %s", req_id_s
                )

        accounts2 = await self.repo.snapshot_wallet(client_id)
        summary = self.notification_builder.cancellation_summary(
            req_id_s,
            accounts2,
            (
                CancelledBalanceLeg(recv_code, recv_amt, recv_prec, recv_op_sign),
                CancelledBalanceLeg(pay_code, pay_amt, pay_prec, pay_op_sign),
            ),
        )
        await replier.reply(summary, parse_mode="HTML")
        await replier.alert("Заявка отменена", modal=False)
        return CancelExchangeResult(ok=True)
