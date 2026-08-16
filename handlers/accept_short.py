from __future__ import annotations

from collections.abc import Iterable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from db_asyncpg.ports.administration import ManagerRepositoryPort
from services.exchange import (
    AcceptShortCommand,
    AcceptShortService,
    CancelExchangeParams,
    ExchangeReplyContext,
)
from telegram_adapters import AiogramCallbackReplier, AiogramMessageReplier, AiogramMessenger
from telegram_adapters.auth import (
    require_manager_or_admin_callback,
    require_manager_or_admin_message,
)
from telegram_adapters.message_context import get_chat_name


def _is_forwarded_message(message: Message) -> bool:
    return any(
        getattr(message, attr, None) is not None
        for attr in (
            "forward_origin",
            "forward_from",
            "forward_from_chat",
            "forward_sender_name",
            "forward_signature",
            "forward_date",
            "forward_from_message_id",
        )
    )


class AcceptShortHandler:
    """
    /пд|/пе|/пт|/пр|/пб <recv_amount_expr> <од|ое|от|ор|об> <pay_amount_expr> [комментарий]

    Принимаем слева — СПИСЫВАЕМ у клиента; отдаём справа — ЗАЧИСЛЯЕМ клиенту.
    Если команда отправлена ответом на карточку бота — редактируем заявку.
    """
    def __init__(
            self,
            repo: ManagerRepositoryPort,
            service: AcceptShortService,
            admin_chat_ids: Iterable[int] | None = None,
            admin_user_ids: Iterable[int] | None = None,
            *,
            ignore_chat_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.service = service
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.ignore_chat_ids = set(ignore_chat_ids or set())
        self.router = Router()
        self._register()

    async def _cmd_accept_short(self, message: Message) -> None:
        # Игнорируем в "шумных" чатах
        if self.ignore_chat_ids and message.chat and message.chat.id in self.ignore_chat_ids:
            return

        # Не реагируем на пересланные партнерские сообщения, даже если их переслал наш менеджер.
        if _is_forwarded_message(message):
            return

        # доступ
        if not await require_manager_or_admin_message(
                self.repo, message,
                admin_chat_ids=self.admin_chat_ids,
                admin_user_ids=self.admin_user_ids,
        ):
            return
        try:
            reply_message = getattr(message, "reply_to_message", None)
            reply = None
            if reply_message and (reply_message.text or ""):
                authored_by_bot = bool(
                    reply_message.from_user
                    and reply_message.from_user.id == message.bot.id
                )
                reply = ExchangeReplyContext(
                    message_id=reply_message.message_id,
                    text=reply_message.text or "",
                    authored_by_bot=authored_by_bot,
                )
            await self.service.execute_command(
                AcceptShortCommand(
                    chat_id=message.chat.id,
                    chat_name=get_chat_name(message),
                    message_id=message.message_id,
                    text=message.text or "",
                    actor_name=_actor_name(message),
                    reply=reply,
                ),
                messenger=AiogramMessenger(message.bot),
                replier=AiogramMessageReplier(message),
            )
        except ValueError as exc:
            await message.answer(str(exc))

    # ====== КОЛЛБЭК ОТМЕНЫ ЗАЯВКИ (делегируем в базовый класс) ======
    async def _cb_cancel(self, cq: CallbackQuery) -> None:
        if not await require_manager_or_admin_callback(
                self.repo, cq,
                admin_chat_ids=self.admin_chat_ids,
                admin_user_ids=self.admin_user_ids,
        ):
            return
        message = cq.message
        if not message or not message.text:
            await cq.answer("Нет сообщения", show_alert=True)
            return
        parts = (cq.data or "").split(":")
        request_id = parts[1].strip() if len(parts) >= 2 else ""
        if not request_id:
            await cq.answer("Некорректные данные", show_alert=True)
            return
        table_request_id = parts[2].strip() if len(parts) >= 3 else None
        is_request_chat = self.service.is_request_chat_origin(message.chat.id)
        await self.service.cancel_exchange_request.execute_core(
            CancelExchangeParams(
                chat_id=message.chat.id,
                chat_name=get_chat_name(message),
                card_message_id=message.message_id,
                card_text=message.text,
                req_id=request_id,
                table_req_id_hint=table_request_id,
                recv_is_deposit=is_request_chat,
                pay_is_withdraw=is_request_chat,
            ),
            messenger=AiogramMessenger(cq.bot),
            replier=AiogramCallbackReplier(cq),
        )

    def _register(self) -> None:
        self.router.message.register(self._cmd_accept_short, Command("пд"))
        self.router.message.register(self._cmd_accept_short, Command("пе"))
        self.router.message.register(self._cmd_accept_short, Command("пт"))
        self.router.message.register(self._cmd_accept_short, Command("пр"))
        self.router.message.register(self._cmd_accept_short, Command("пб"))
        self.router.message.register(self._cmd_accept_short, Command("пп"))
        self.router.message.register(self._cmd_accept_short, Command("прмск"))
        self.router.message.register(self._cmd_accept_short, Command("прспб"))
        self.router.message.register(self._cmd_accept_short, Command("прпер"))
        self.router.message.register(
            self._cmd_accept_short,
            F.text.regexp(r"(?iu)^/(пд|пе|пт|пр|пб|прмск|прспб|прпер|пп)(?:@\w+)?\b"),
        )
        self.router.callback_query.register(self._cb_cancel, F.data.startswith("req_cancel:"))


def _actor_name(message: Message) -> str:
    user = message.from_user
    if not user:
        return "unknown"
    return user.full_name or (f"@{user.username}" if user.username else f"id:{user.id}")
