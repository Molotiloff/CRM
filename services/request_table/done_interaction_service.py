from __future__ import annotations

import logging
from collections.abc import Iterable

from db_asyncpg.ports.exchange import ExchangeRequestRepositoryPort
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
from services.request_table.table_done_service import RequestTableDoneService, TableDonePayload


class RequestTableDoneInteractionService:
    _SCOPE = "table_done"

    def __init__(
        self,
        *,
        repo: ExchangeRequestRepositoryPort,
        request_chat_ids: Iterable[int],
        done_service: RequestTableDoneService,
        session_store: RequestTableSessionStore,
        message_builder: RequestTableMessageBuilder,
        sheets_gateway: AsyncSheetsTradeGateway,
        keyboards: RequestTableKeyboardPort | None = None,
    ) -> None:
        self.repo = repo
        self.allowed = {int(x) for x in request_chat_ids}
        self.done_service = done_service
        self.session_store = session_store
        self.message_builder = message_builder
        self.sheets_gateway = sheets_gateway
        self.keyboards = keyboards or NullRequestTableKeyboardPresenter()

    @logged_operation("request_table.done")
    async def handle(
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
        if (
            self.message_builder.STATUS_LINE_DONE in command.message_text
            or self.session_store.is_marked(self._SCOPE, key)
        ):
            await replier.alert("Статус уже проставлен.", modal=False)
            return

        parsed = await self._payload_from_callback_or_db(command.callback_data)
        if not parsed:
            logging.error("Invalid table_done callback payload: %r", command.callback_data)
            await replier.alert(
                "Не удалось найти параметры заявки в БД. В таблицу не записано.",
                modal=True,
            )
            return
        bind_log_context(request_id=parsed.req_id)

        try:
            await messenger.edit_reply_markup(
                chat_id=command.chat_id,
                message_id=command.message_id,
                reply_markup=self.keyboards.processing(),
            )
        except MessengerError:
            with suppress_benign_messenger_errors(context="table done: strip keyboard"):
                await messenger.edit_reply_markup(
                    chat_id=command.chat_id,
                    message_id=command.message_id,
                    reply_markup=None,
                )

        self.session_store.add_pending(self._SCOPE, key)
        try:
            result = await self.done_service.write_by_payload(
                payload=parsed,
                message_dt=command.message_dt,
            )
            if parsed.req_id is not None:
                await self.repo.mark_exchange_request_table_done(
                    table_req_id=str(parsed.req_id),
                    is_table_done=True,
                )
        except SheetsWriteError as e:
            logging.exception("Sheets write failed: %s", e)
            error_text = str(e).lower()
            if "permission" in error_text or "forbidden" in error_text:
                sa_email = await self.sheets_gateway.get_service_account_email() or (
                    "service-account@<project>.iam.gserviceaccount.com"
                )
                await replier.alert(
                    self.message_builder.short(
                        f"Нет доступа к таблице.\nВыдайте право «Редактор» для:\n{sa_email}"
                    ),
                    modal=True,
                )
            else:
                await replier.alert(self.message_builder.short(str(e)), modal=True)
            return
        except Exception as e:
            logging.exception("Unexpected error while writing to Sheets: %s", e)
            await replier.alert(
                self.message_builder.short("Не удалось записать в таблицу."),
                modal=True,
            )
            return
        finally:
            self.session_store.discard_pending(self._SCOPE, key)

        new_text = self.message_builder.append_status_once(
            command.message_text,
            self.message_builder.STATUS_LINE_DONE,
        )
        with suppress_benign_messenger_errors(context="table done: write status"):
            await messenger.edit_text(
                chat_id=command.chat_id,
                message_id=command.message_id,
                text=new_text,
            )
            self.session_store.mark(self._SCOPE, key)
        with suppress_benign_messenger_errors(context="table done: strip keyboard"):
            await messenger.edit_reply_markup(
                chat_id=command.chat_id,
                message_id=command.message_id,
                reply_markup=None,
            )

        await replier.alert(
            self.message_builder.done_summary(result=result),
            modal=False,
        )

    async def _payload_from_callback_or_db(self, data: str) -> TableDonePayload | None:
        parsed = self.done_service.parse_callback_payload(data)
        if parsed:
            return parsed

        table_req_id = self.done_service.parse_table_req_id(data)
        if not table_req_id:
            return None

        row = await self.repo.get_exchange_request_link_by_table_req_id(table_req_id=table_req_id)
        if not row:
            return None
        return self.done_service.payload_from_db_row(row)
