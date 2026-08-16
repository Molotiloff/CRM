from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InputMediaPhoto

from services.broadcast import BroadcastDeliveryStatus
from telegram_adapters.broadcast_models import (
    BroadcastPayload,
    PhotoBroadcastPayload,
    TextBroadcastPayload,
)

logger = logging.getLogger(__name__)


class AiogramBroadcastDelivery:
    def __init__(self, *, bot: Bot, payload: BroadcastPayload) -> None:
        self.bot = bot
        self.payload = payload

    async def send(self, *, chat_id: int) -> BroadcastDeliveryStatus:
        try:
            await self._send(chat_id=chat_id)
            return BroadcastDeliveryStatus.SENT
        except TelegramForbiddenError:
            return BroadcastDeliveryStatus.BLOCKED
        except TelegramBadRequest as exc:
            text = str(exc).lower()
            if "chat not found" in text or "bot was blocked" in text:
                return BroadcastDeliveryStatus.NOT_FOUND
            logger.exception("TelegramBadRequest for chat_id=%s", chat_id)
            return BroadcastDeliveryStatus.FAILED
        except Exception:
            logger.exception("Unexpected broadcast error for chat_id=%s", chat_id)
            return BroadcastDeliveryStatus.FAILED

    async def _send(self, *, chat_id: int) -> None:
        if isinstance(self.payload, TextBroadcastPayload):
            await self.bot.send_message(
                chat_id=chat_id,
                text=self.payload.text,
                entities=list(self.payload.entities),
            )
            return

        if isinstance(self.payload, PhotoBroadcastPayload):
            await self.bot.send_photo(
                chat_id=chat_id,
                photo=self.payload.file_id,
                caption=self.payload.caption,
                caption_entities=list(self.payload.caption_entities),
            )
            return

        media = [
            InputMediaPhoto(
                media=file_id,
                caption=self.payload.caption if index == 0 else None,
                caption_entities=(
                    list(self.payload.caption_entities) if index == 0 else None
                ),
            )
            for index, file_id in enumerate(self.payload.file_ids)
        ]
        await self.bot.send_media_group(chat_id=chat_id, media=media)
