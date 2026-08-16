from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from services.rate_order.models import LiveMessageEditResult, LiveMessageEditStatus

log = logging.getLogger("orderbook_service")


class AiogramOrderbookLiveMessageEditor:
    def __init__(self, *, bot: Bot) -> None:
        self.bot = bot

    async def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> LiveMessageEditResult:
        try:
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
            )
            return LiveMessageEditResult(LiveMessageEditStatus.UPDATED)
        except TelegramRetryAfter as exc:
            return LiveMessageEditResult(
                LiveMessageEditStatus.RETRY,
                retry_after_seconds=float(exc.retry_after),
            )
        except TelegramBadRequest as exc:
            error = str(exc).lower()
            if "message is not modified" in error:
                return LiveMessageEditResult(LiveMessageEditStatus.UNCHANGED)
            if "message to edit not found" in error or "chat not found" in error:
                return LiveMessageEditResult(LiveMessageEditStatus.MISSING)
            log.warning(
                "Failed to refresh live message chat_id=%s message_id=%s: %r",
                chat_id,
                message_id,
                exc,
            )
        except (TelegramNetworkError, TelegramServerError) as exc:
            log.warning(
                "Telegram unavailable while refreshing live message "
                "chat_id=%s message_id=%s: %r",
                chat_id,
                message_id,
                exc,
            )
        except Exception:
            log.exception(
                "Unexpected error updating live message chat_id=%s message_id=%s",
                chat_id,
                message_id,
            )
        return LiveMessageEditResult(LiveMessageEditStatus.FAILED)
