from __future__ import annotations

import logging
from collections.abc import Iterable

from gutils.requests_sheet import SheetsWriteError
from observability import bind_log_context, logged_operation
from services.messaging import (
    MessengerError,
    MessengerPort,
    ReplierPort,
    suppress_benign_messenger_errors,
)
from services.request_table.keyboard_port import (
    NullRequestTableKeyboardPresenter,
    RequestTableKeyboardPort,
)
from services.request_table.message_builder import RequestTableMessageBuilder
from services.request_table.models import RequestTableCallbackCommand
from services.request_table.session_store import RequestTableSessionStore
from services.request_table.sheets_trade_gateway import AsyncSheetsTradeGateway


class RequestTableDeleteInteractionService:
    _SCOPE = "table_delete"

    def __init__(
        self,
        *,
        request_chat_ids: Iterable[int],
        session_store: RequestTableSessionStore,
        message_builder: RequestTableMessageBuilder,
        sheets_gateway: AsyncSheetsTradeGateway,
        keyboards: RequestTableKeyboardPort | None = None,
    ) -> None:
        self.allowed = {int(x) for x in request_chat_ids}
        self.session_store = session_store
        self.message_builder = message_builder
        self.sheets_gateway = sheets_gateway
        self.keyboards = keyboards or NullRequestTableKeyboardPresenter()

    async def handle_no(
        self,
        command: RequestTableCallbackCommand,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
    ) -> None:
        if command.chat_id not in self.allowed:
            await replier.alert("Недоступно здесь.", modal=True)
            return
        with suppress_benign_messenger_errors(context="table delete: keep rows"):
            await messenger.edit_reply_markup(
                chat_id=command.chat_id,
                message_id=command.message_id,
                reply_markup=None,
            )
        await replier.alert("Оставляем строки в таблицах.", modal=False)

    @logged_operation("request_table.delete")
    async def handle_yes(
        self,
        command: RequestTableCallbackCommand,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
    ) -> None:
        if command.chat_id not in self.allowed:
            await replier.alert("Недоступно здесь.", modal=True)
            return

        key = (command.chat_id, command.message_id)
        if self.session_store.is_pending(self._SCOPE, key):
            await replier.alert("⏳ Уже обрабатывается…", modal=False)
            return

        try:
            await messenger.edit_reply_markup(
                chat_id=command.chat_id,
                message_id=command.message_id,
                reply_markup=self.keyboards.processing(),
            )
        except MessengerError:
            with suppress_benign_messenger_errors(context="table delete: strip keyboard"):
                await messenger.edit_reply_markup(
                    chat_id=command.chat_id,
                    message_id=command.message_id,
                    reply_markup=None,
                )

        self.session_store.add_pending(self._SCOPE, key)
        try:
            req_id = self._parse_req_id(command.callback_data)
            bind_log_context(request_id=req_id)
        except ValueError:
            self.session_store.discard_pending(self._SCOPE, key)
            await replier.alert("Некорректные данные", modal=True)
            return

        try:
            res = await self.sheets_gateway.delete_rows_by_request_id(
                req_id=req_id,
                spreadsheet=None,
                sheets=("Покупка", "Продажа"),
            )
        except SheetsWriteError as e:
            logging.exception("Sheets delete failed: %s", e)
            await replier.alert("Не удалось удалить из Google Sheets.", modal=True)
            return
        except Exception as e:
            logging.exception("Unexpected delete error: %s", e)
            await replier.alert("Ошибка при удалении.", modal=True)
            return
        finally:
            self.session_store.discard_pending(self._SCOPE, key)

        new_text = self.message_builder.append_status_once(
            command.message_text,
            self.message_builder.deleted_status(req_id),
        )
        try:
            await messenger.edit_text(
                chat_id=command.chat_id,
                message_id=command.message_id,
                text=new_text,
                reply_markup=None,
            )
            self.session_store.mark(self._SCOPE, key)
        except MessengerError:
            with suppress_benign_messenger_errors(
                context="table delete: strip keyboard after status"
            ):
                await messenger.edit_reply_markup(
                    chat_id=command.chat_id,
                    message_id=command.message_id,
                    reply_markup=None,
                )

        await replier.alert(
            self.message_builder.deleted_summary(
                deleted_buy=res.get("Покупка", 0),
                deleted_sale=res.get("Продажа", 0),
            ),
            modal=False,
        )

    @staticmethod
    def _parse_req_id(data: str) -> str:
        parts = (data or "").split(":")
        req_id = parts[-1].strip() if parts else ""
        if not req_id:
            raise ValueError("empty req_id")
        return req_id
