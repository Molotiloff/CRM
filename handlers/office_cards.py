from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OfficeCard:
    command: str
    caption: str
    # основной способ (без аплоада)
    photo_file_id: str | None = None
    # fallback (на время миграции / если file_id ещё не задан)
    image_path: Path | None = None


class OfficeCardsHandler:
    """
    Генератор команд вида /екб, /члб, ...
    Каждая команда отправляет:
      1) фото + текст, если есть photo_file_id
      2) фото + текст, если есть image_path
      3) просто текст, если картинка не задана

    Приоритет:
      1) photo_file_id
      2) image_path
      3) caption как обычное сообщение
    """

    def __init__(self, cards: Mapping[str, OfficeCard]) -> None:
        self.router = Router()
        self.cards: dict[str, OfficeCard] = {
            k.strip().lower(): v for k, v in cards.items()
        }
        self._register()

    def _register(self) -> None:
        for cmd in self.cards:
            self.router.message.register(
                self._send_card,
                Command(cmd, ignore_mention=True),
            )

    async def _send_card(self, message: Message) -> None:
        text = (message.text or "").strip()
        if not text.startswith("/"):
            return

        cmd_token = text[1:].split(None, 1)[0]
        cmd = cmd_token.split("@", 1)[0].strip().lower()

        card = self.cards.get(cmd)
        if not card:
            return

        # 1) отправка по file_id
        if card.photo_file_id:
            await message.answer_photo(
                photo=card.photo_file_id,
                caption=card.caption,
                parse_mode="HTML",
            )
            return

        # 2) fallback на локальный файл
        if card.image_path:
            if not card.image_path.exists():
                await message.answer(
                    f"Файл не найден: {card.image_path.as_posix()}",
                )
                return

            sent = await message.answer_photo(
                photo=FSInputFile(card.image_path),
                caption=card.caption,
                parse_mode="HTML",
            )

            if sent.photo:
                fid = sent.photo[-1].file_id
                log.debug("office_cards command=/%s file_id=%s", cmd, fid)
            return

        # 3) если картинки нет — просто отправляем текст
        await message.answer(
            card.caption,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
