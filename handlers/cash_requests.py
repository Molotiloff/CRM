from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from db_asyncpg.ports.administration import ManagerRepositoryPort
from services.cash_requests import (
    CashRequestService,
    CreateCashRequestParams,
    EditCashRequestParams,
    RequestDealCancelService,
    RequestDealDoneService,
    RequestIssueParams,
    RequestIssueService,
    RequestTimeParams,
    RequestTimeService,
)
from services.cash_requests.constants import CB_DEAL_CANCEL, CB_DEAL_DONE, CB_ISSUE_DONE
from services.cash_requests.workflow_models import CashRequestStatusCommand
from telegram_adapters import (
    AiogramCallbackReplier,
    AiogramMessageReplier,
    AiogramMessenger,
)
from telegram_adapters.auth import (
    require_manager_or_admin_callback,
    require_manager_or_admin_message,
)
from telegram_adapters.message_context import actor_from_message, get_chat_name


class CashRequestsHandler:
    def __init__(
        self,
        *,
        repo: ManagerRepositoryPort,
        admin_chat_ids: set[int],
        admin_user_ids: set[int],
        request_service: CashRequestService,
        request_time_service: RequestTimeService,
        request_issue_service: RequestIssueService,
        request_deal_done_service: RequestDealDoneService,
        request_deal_cancel_service: RequestDealCancelService,
    ) -> None:
        self.router = Router()
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids)
        self.admin_user_ids = set(admin_user_ids)
        self.request_service = request_service
        self.request_time_service = request_time_service
        self.request_issue_service = request_issue_service
        self.request_deal_done_service = request_deal_done_service
        self.request_deal_cancel_service = request_deal_cancel_service
        self._register()

    async def _request(self, message: Message) -> None:
        if not await self._authorize_message(message):
            return
        parsed = self.request_service.parse_command(message.text or "")
        if parsed is None:
            await message.answer(self.request_service.help_text())
            return
        messenger = AiogramMessenger(message.bot)
        replier = AiogramMessageReplier(message)

        async def sync_schedule(city: str) -> None:
            await self.request_service.schedule_service.sync_board(
                messenger=messenger,
                city=city,
            )

        reply = message.reply_to_message
        is_bot_reply = bool(
            reply
            and reply.from_user
            and reply.from_user.id == message.bot.id
            and (reply.text or reply.caption)
        )
        if is_bot_reply and reply is not None:
            await self.request_service.edit_cash_request.execute_core(
                EditCashRequestParams(
                    chat_id=message.chat.id,
                    chat_name=get_chat_name(message),
                    parsed=parsed,
                    old_text=_plain_text(reply),
                    reply_msg_id=reply.message_id,
                    editor_name=actor_from_message(message),
                ),
                messenger=messenger,
                replier=replier,
                sync_schedule_board=sync_schedule,
                sync_old_city_schedule=sync_schedule,
            )
            return
        await self.request_service.create_cash_request.execute_core(
            CreateCashRequestParams(
                chat_id=message.chat.id,
                chat_name=get_chat_name(message),
                parsed=parsed,
                creator_name=actor_from_message(message),
                source_message_id=message.message_id,
            ),
            messenger=messenger,
            replier=replier,
            sync_schedule_board=sync_schedule,
        )

    async def _time(self, message: Message) -> None:
        if not await self._authorize_message(message):
            return
        if not self.request_time_service.router_service.is_request_chat(message.chat.id):
            return
        hhmm = self.request_time_service.parse_time((message.text or "").strip())
        if hhmm is None:
            await message.answer("Формат: /время 10:00")
            return
        reply = message.reply_to_message
        if reply is None:
            await message.answer("Нужно ответить командой /время на сообщение с заявкой.")
            return
        target_html, is_caption = _html_text(reply)
        if not target_html.strip():
            await message.answer("Нужно ответить на сообщение с текстом.")
            return
        messenger = AiogramMessenger(message.bot)

        async def sync_schedule(city: str) -> None:
            await self.request_time_service.schedule_service.sync_board(
                messenger=messenger,
                city=city,
            )

        await self.request_time_service.execute_core(
            RequestTimeParams(
                request_chat_id=message.chat.id,
                target_chat_id=reply.chat.id,
                target_message_id=reply.message_id,
                target_text_html=target_html,
                target_text_plain=_plain_text(reply),
                is_caption=is_caption,
                reply_markup=reply.reply_markup,
                hhmm=hhmm,
            ),
            messenger=messenger,
            replier=AiogramMessageReplier(message),
            sync_schedule_board=sync_schedule,
        )

    async def _issue(self, callback: CallbackQuery) -> None:
        if not await self._authorize_callback(callback):
            return
        message = callback.message
        if message is None:
            await callback.answer()
            return
        await self.request_issue_service.execute_core(
            RequestIssueParams(
                chat_id=message.chat.id,
                chat_name=get_chat_name(message),
                message_id=message.message_id,
                card_text=message.text or "",
                callback_data=callback.data or "",
            ),
            messenger=AiogramMessenger(callback.bot),
            replier=AiogramCallbackReplier(callback),
        )

    async def _done(self, callback: CallbackQuery) -> None:
        await self._status(callback, self.request_deal_done_service)

    async def _cancel(self, callback: CallbackQuery) -> None:
        await self._status(callback, self.request_deal_cancel_service)

    async def _status(self, callback: CallbackQuery, service) -> None:
        if not await self._authorize_callback(callback):
            return
        message = callback.message
        if message is None:
            await callback.answer()
            return
        if not service.router_service.is_request_chat(message.chat.id):
            await callback.answer(
                "Кнопка доступна только в чате сделок.",
                show_alert=True,
            )
            return
        card_text, is_caption = _html_text(message)
        messenger = AiogramMessenger(callback.bot)

        async def sync_schedule(city: str) -> None:
            await service.schedule_service.sync_board(messenger=messenger, city=city)

        await service.execute_core(
            CashRequestStatusCommand(
                chat_id=message.chat.id,
                message_id=message.message_id,
                card_text=card_text,
                is_caption=is_caption,
                callback_data=callback.data or "",
            ),
            messenger=messenger,
            replier=AiogramCallbackReplier(callback),
            sync_schedule_board=sync_schedule,
        )

    async def _authorize_message(self, message: Message) -> bool:
        return await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        )

    async def _authorize_callback(self, callback: CallbackQuery) -> bool:
        return await require_manager_or_admin_callback(
            self.repo,
            callback,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        )

    def _register(self) -> None:
        self.router.message.register(
            self._request,
            Command(*self.request_service.supported_commands),
        )
        self.router.message.register(self._time, Command("время"))
        self.router.callback_query.register(
            self._issue,
            F.data.startswith(CB_ISSUE_DONE),
        )
        self.router.callback_query.register(
            self._done,
            F.data.startswith(CB_DEAL_DONE),
        )
        self.router.callback_query.register(
            self._cancel,
            F.data.startswith(CB_DEAL_CANCEL),
        )


def _plain_text(message: Message) -> str:
    return message.caption or "" if message.caption is not None and not message.text else message.text or ""


def _html_text(message: Message) -> tuple[str, bool]:
    if message.caption is not None and not message.text:
        return (message.html_caption or message.caption or ""), True
    return (message.html_text or message.text or ""), False
