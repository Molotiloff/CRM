"""Handle Bot API 10.4 updates preserved by aiogram's extra='allow'."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Chat, Update, User
from pydantic import BaseModel, Field, ValidationError

from db_asyncpg.ports.administration import ManagerRepositoryPort
from services.rate_order.rapira_widget_service import RapiraWidgetService

log = logging.getLogger(__name__)


class WidgetQueryCompat(BaseModel):
    id: str
    sender: User = Field(alias="from")
    chat: Chat | None = None
    message_id: int | None = None
    data: str = Field(max_length=64)
    type: dict[str, Any]


class WidgetQueryMiddleware(BaseMiddleware):
    def __init__(
        self,
        service: RapiraWidgetService,
        repo: ManagerRepositoryPort,
        *,
        admin_ids: list[int],
        admin_chat_ids: list[int],
    ) -> None:
        self.service, self.repo = service, repo
        self.admin_ids, self.admin_chats = frozenset(admin_ids), frozenset(admin_chat_ids)

    async def __call__(self, handler, event, data):
        raw = getattr(event, "widget_query", None) if isinstance(event, Update) else None
        if not isinstance(raw, dict):
            return await handler(event, data)
        try:
            query = WidgetQueryCompat.model_validate(raw)
        except ValidationError:
            log.warning("Malformed widget_query ignored")
            return True
        if query.chat is None or query.message_id is None:
            await self.service.api.answer(query.id, '{"error":"Unsupported widget context"}')
            return True
        permitted = (
            query.type.get("type") == "data"
            and not query.sender.is_bot
            and (
                query.chat.id in self.admin_chats
                or query.sender.id in self.admin_ids
                or await self.repo.is_manager(query.sender.id)
            )
        )
        await self.service.query(
            query_id=query.id,
            user_id=query.sender.id,
            chat_id=query.chat.id,
            message_id=query.message_id,
            data=query.data,
            authorized=permitted,
        )
        return True
