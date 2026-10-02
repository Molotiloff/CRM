from __future__ import annotations

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from services.admin_client.invite_link_backfill import (
    ClientChatLinkInfo,
    InviteLinkRateLimited,
    InviteLinkUnavailable,
)


class AiogramClientInviteLinkGateway:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def get_chat_info(self, chat_id: int) -> ClientChatLinkInfo:
        try:
            chat = await self._bot.get_chat(chat_id, request_timeout=10)
        except TelegramRetryAfter as exc:
            raise InviteLinkRateLimited(exc.retry_after) from exc
        except TelegramAPIError as exc:
            raise InviteLinkUnavailable(str(exc)) from exc
        return ClientChatLinkInfo(
            is_group=chat.type in {ChatType.GROUP, ChatType.SUPERGROUP},
            username=chat.username,
        )

    async def create_approval_link(self, chat_id: int) -> str:
        try:
            created = await self._bot.create_chat_invite_link(
                chat_id,
                name="SkyEx CRM approval",
                creates_join_request=True,
                request_timeout=10,
            )
        except TelegramRetryAfter as exc:
            raise InviteLinkRateLimited(exc.retry_after) from exc
        except TelegramAPIError as exc:
            raise InviteLinkUnavailable(str(exc)) from exc
        return created.invite_link
