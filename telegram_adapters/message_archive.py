from __future__ import annotations

from collections.abc import AsyncIterator

from aiogram import Bot
from aiogram.types import Message

from services.message_archive.models import ArchiveMessage
from services.message_archive.normalizer import MessageArchiveNormalizer


class AiogramMessageArchiveNormalizer:
    @staticmethod
    def normalize(message: Message, *, outbound: bool) -> ArchiveMessage:
        return MessageArchiveNormalizer.from_bot_message(message, outbound=outbound)


class AiogramArchiveMediaSource:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def stream(self, file_id: str) -> AsyncIterator[bytes]:
        telegram_file = await self._bot.get_file(file_id)
        if not telegram_file.file_path:
            raise FileNotFoundError("Telegram did not return file_path")
        url = self._bot.session.api.file_url(self._bot.token, telegram_file.file_path)
        async for chunk in self._bot.session.stream_content(url, chunk_size=64 * 1024):
            yield chunk
