from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain import TelegramMessageRef
from observability import bind_log_context, logged_operation, measured_operation
from services.exchange.transaction_service import EditExchangeTransaction
from services.exchange.use_case_base import _ExchangeUseCaseBase
from services.exchange.workflow_policy import is_request_chat, tracked_exchange_currencies
from services.messaging import MessengerError, MessengerPort, ReplierPort

log = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class EditExchangeParams:
    """Транспортно-независимый вход редактирования заявки (C0.3).

    old_card_text — прежний текст карточки: из него считаются реверс-дельты ног
    (не-Telegram вызыватель берёт его из exchange_request_links.request_text).
    target_bot_msg_id — id карточки (правится текст);
    cmd_msg_id — уникальный id команды: идемпотентность дельт.
    """

    chat_id: int
    chat_name: str
    edit_req_id: str
    target_bot_msg_id: int
    old_card_text: str
    cmd_msg_id: int
    recv_code: str
    pay_code: str
    recv_amount: Decimal
    pay_amount: Decimal
    recv_prec: int
    pay_prec: int
    rate_str: str
    user_note: str | None = None
    creator_name: str = "unknown"
    recv_is_deposit: bool = True
    pay_is_withdraw: bool = True


@dataclass(slots=True, frozen=True)
class EditExchangeResult:
    ok: bool
    error: str | None = None


class EditExchangeRequest(_ExchangeUseCaseBase):
    @logged_operation("exchange.edit")
    @measured_operation("exchange.edit")
    async def execute_core(
        self,
        params: EditExchangeParams,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
    ) -> EditExchangeResult:
        chat_id = params.chat_id
        chat_name = params.chat_name
        edit_req_id = params.edit_req_id
        bind_log_context(request_id=edit_req_id)
        target_bot_msg_id = params.target_bot_msg_id
        recv_code, pay_code = params.recv_code, params.pay_code
        recv_amount, pay_amount = params.recv_amount, params.pay_amount
        recv_prec, pay_prec = params.recv_prec, params.pay_prec
        rate_str = params.rate_str

        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)

        meta = await self._get_exchange_request_meta(edit_req_id)
        table_req_id = (meta.table_request_id if meta else None) or edit_req_id

        changed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        single_request_chat_card = is_request_chat(chat_id, self.request_chat_id)
        new_client_text = self.text_builder.build_client_text(
            req_id=edit_req_id,
            recv_code=recv_code,
            recv_amount=recv_amount,
            recv_prec=recv_prec,
            pay_code=pay_code,
            pay_amount=pay_amount,
            pay_prec=pay_prec,
            rate=rate_str,
            note=params.user_note,
            changed_at=changed_at,
        )
        request_text = self.text_builder.build_request_text(
            req_id=edit_req_id,
            table_req_id=table_req_id,
            client_name=chat_name,
            recv_code=recv_code,
            recv_amount=recv_amount,
            recv_prec=recv_prec,
            pay_code=pay_code,
            pay_amount=pay_amount,
            pay_prec=pay_prec,
            rate=rate_str,
            creator_name=params.creator_name,
            note=params.user_note,
            changed_at=changed_at,
        )

        applied_movements = []
        try:
            transaction_result = await self.transaction_service.edit(
                EditExchangeTransaction(
                    request_id=edit_req_id,
                    table_request_id=str(table_req_id),
                    client_id=client_id,
                    old_card_text=params.old_card_text,
                    receive_code=recv_code,
                    receive_amount=recv_amount,
                    receive_precision=recv_prec,
                    pay_code=pay_code,
                    pay_amount=pay_amount,
                    pay_precision=pay_prec,
                    rate=Decimal(rate_str.replace(",", ".")),
                    chat_id=chat_id,
                    card_message_id=target_bot_msg_id,
                    command_message_id=params.cmd_msg_id,
                    receive_is_deposit=params.recv_is_deposit,
                    pay_is_withdraw=params.pay_is_withdraw,
                    tracked_currency_codes=tracked_exchange_currencies(
                        chat_id, self.request_chat_id
                    ),
                )
            )
            applied_movements = list(transaction_result.movements)
            log.info(
                "Exchange request %s edited (movements=%d)", edit_req_id, len(applied_movements)
            )
        except Exception as e:
            log.exception("apply_edit_delta failed for exchange request %s", edit_req_id)
            await replier.reply(f"Не удалось пересчитать балансы: {e}")
            return EditExchangeResult(ok=False, error=str(e))

        if single_request_chat_card:
            try:
                await messenger.edit_text(
                    chat_id,
                    target_bot_msg_id,
                    request_text,
                    reply_markup=self.keyboards.request_chat(
                        request_id=edit_req_id,
                        table_request_id=table_req_id,
                    ),
                )
            except MessengerError as e:
                log.warning(
                    "Failed to edit request-chat card for exchange request %s: %r", edit_req_id, e
                )
                await replier.reply(f"Не удалось изменить заявку: {e}")
                return EditExchangeResult(ok=False, error=str(e))

            await self.source_links.bind_client_card(
                request_id=edit_req_id,
                table_request_id=str(table_req_id),
                message=TelegramMessageRef(chat_id, target_bot_msg_id),
                request_text=request_text,
                is_request_card=True,
            )
            if self.act_counter_service and applied_movements:
                await self.act_counter_service.register_exchange_movements(
                    req_id=edit_req_id,
                    table_req_id=str(table_req_id),
                    request_chat_id=int(chat_id),
                    request_message_id=int(target_bot_msg_id),
                    movements=applied_movements,
                )
            if self.act_counter_service:
                await self._notify_act_current_amount(
                    messenger=messenger,
                    request_chat_id=int(chat_id),
                )
        else:
            try:
                await messenger.edit_text(
                    chat_id,
                    target_bot_msg_id,
                    new_client_text,
                    reply_markup=self.keyboards.client_cancel(
                        request_id=edit_req_id,
                        table_request_id=table_req_id,
                    ),
                )
            except MessengerError as e:
                log.warning(
                    "Failed to edit client card for exchange request %s: %r", edit_req_id, e
                )
                await replier.reply(f"Не удалось изменить заявку: {e}")
                return EditExchangeResult(ok=False, error=str(e))

        if single_request_chat_card:
            try:
                await messenger.send(
                    chat_id,
                    self.notification_builder.edit_notice(edit_req_id),
                )
            except Exception:
                log.exception(
                    "Failed to post edit notification for exchange request %s", edit_req_id
                )

        if self.request_chat_id and not single_request_chat_card:
            try:
                request_copy = None
                if meta and meta.request_message:
                    request_copy = (
                        meta.request_message.chat_id,
                        meta.request_message.message_id,
                    )
                if request_copy is not None:
                    request_chat_id, request_message_id = request_copy
                    await messenger.edit_text(
                        request_chat_id,
                        request_message_id,
                        request_text,
                        reply_markup=self.keyboards.table_create(
                            receive_code=recv_code,
                            pay_code=pay_code,
                            receive_amount=recv_amount,
                            pay_amount=pay_amount,
                            rate=rate_str,
                            table_request_id=table_req_id,
                        ),
                    )
                    await self.source_links.bind_request_card(
                        request_id=edit_req_id,
                        table_request_id=str(table_req_id),
                        message=TelegramMessageRef(request_chat_id, request_message_id),
                        request_text=request_text,
                    )
                    if self.act_counter_service and applied_movements:
                        await self.act_counter_service.register_exchange_movements(
                            req_id=edit_req_id,
                            table_req_id=str(table_req_id),
                            request_chat_id=int(request_chat_id),
                            request_message_id=int(request_message_id),
                            movements=applied_movements,
                        )
                        if int(chat_id) != int(request_chat_id):
                            await self.act_counter_service.apply_request_wallet_movements(
                                req_id=edit_req_id,
                                table_req_id=str(table_req_id),
                                request_chat_id=int(request_chat_id),
                                request_message_id=int(request_message_id),
                                movements=applied_movements,
                            )
                    if self.act_counter_service:
                        await self._notify_act_current_amount(
                            messenger=messenger,
                            request_chat_id=int(request_chat_id),
                        )
                else:
                    sent_request = await messenger.send(
                        self.request_chat_id,
                        request_text,
                        reply_markup=self.keyboards.table_create(
                            receive_code=recv_code,
                            pay_code=pay_code,
                            receive_amount=recv_amount,
                            pay_amount=pay_amount,
                            rate=rate_str,
                            table_request_id=table_req_id,
                        ),
                    )
                    await self.source_links.bind_request_card(
                        request_id=edit_req_id,
                        table_request_id=str(table_req_id),
                        message=TelegramMessageRef(sent_request.chat_id, sent_request.message_id),
                        request_text=request_text,
                    )
                    if self.act_counter_service and applied_movements:
                        await self.act_counter_service.register_exchange_movements(
                            req_id=edit_req_id,
                            table_req_id=str(table_req_id),
                            request_chat_id=int(sent_request.chat_id),
                            request_message_id=int(sent_request.message_id),
                            movements=applied_movements,
                        )
                        if int(chat_id) != int(sent_request.chat_id):
                            await self.act_counter_service.apply_request_wallet_movements(
                                req_id=edit_req_id,
                                table_req_id=str(table_req_id),
                                request_chat_id=int(sent_request.chat_id),
                                request_message_id=int(sent_request.message_id),
                                movements=applied_movements,
                                chat_name=sent_request.chat_title,
                            )
                    if self.act_counter_service:
                        await self._notify_act_current_amount(
                            messenger=messenger,
                            request_chat_id=int(sent_request.chat_id),
                        )
                await messenger.send(
                    self.request_chat_id,
                    self.notification_builder.edit_notice(edit_req_id),
                )
            except Exception:
                log.exception(
                    "Failed to update request chat copy for edited exchange request %s", edit_req_id
                )

        if not single_request_chat_card:
            rows = await self.repo.snapshot_wallet(client_id)
            await replier.reply(
                self.wallet_presenter.summary(chat_name, rows),
                parse_mode="HTML",
            )

        return EditExchangeResult(ok=True)
