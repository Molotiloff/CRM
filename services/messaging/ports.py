"""Порты обмена сообщениями (workflow_crm.md C0.3, workflow.md 4.2).

Use-cases зависят от этих протоколов, а не от aiogram. Telegram-реализации
находятся в ``telegram_adapters``; CRM использует deferred/outbox-реализации,
а тесты — фейки.

`MessengerPort` — карточки/уведомления в произвольные чаты.
`ReplierPort` — ответы инициатору операции (в боте это message.answer/cq.answer).
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class SentMessageRef:
    """Транспортно-независимая ссылка на отправленное сообщение."""

    chat_id: int
    message_id: int
    chat_title: str | None = None


class MessengerError(Exception):
    """Ошибка транспорта при send/edit.

    `benign=True` — сообщение исчезло/не изменилось/бот выгнан (набор
    SUPPRESSIBLE_TELEGRAM_ERRORS из telegram_adapters.errors): такие можно осознанно
    глотать. Всё остальное (rate limit, сеть, 5xx) глотать нельзя.
    """

    def __init__(self, message: str, *, benign: bool = False) -> None:
        super().__init__(message)
        self.benign = benign


@contextmanager
def suppress_benign_messenger_errors(
    *,
    context: str = "",
    log_level: int = logging.DEBUG,
) -> Iterator[None]:
    """Глотает benign-ошибки транспорта, логирует их, остальные пробрасывает.

    Messaging-аналог telegram_adapters.errors.suppress_telegram_edit_errors: применять
    только вокруг edit/delete/strip-keyboard, где исчезнувшее или неизменившееся
    сообщение — приемлемый исход::

        with suppress_benign_messenger_errors(context="deal cancel strip"):
            await messenger.edit_reply_markup(chat_id=..., message_id=..., reply_markup=None)
    """
    try:
        yield
    except MessengerError as exc:
        if not exc.benign:
            raise
        log.log(
            log_level,
            "Ignored benign messenger error%s: %s",
            f" ({context})" if context else "",
            exc,
        )


class MessengerPort(Protocol):
    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
        reply_to_message_id: int | None = None,
        disable_notification: bool = False,
    ) -> SentMessageRef: ...

    async def send_photo(
        self,
        chat_id: int,
        photo_bytes: bytes,
        *,
        filename: str,
        caption: str | None = None,
        reply_markup: Any | None = None,
        reply_to_message_id: int | None = None,
        disable_notification: bool = False,
    ) -> SentMessageRef: ...

    async def edit_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
        preserve_reply_markup: bool = False,
    ) -> None: ...

    async def edit_caption(
        self,
        chat_id: int,
        message_id: int,
        caption: str,
        *,
        reply_markup: Any | None = None,
        preserve_reply_markup: bool = False,
    ) -> None: ...

    async def edit_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: Any | None = None,
    ) -> None: ...

    async def delete(self, chat_id: int, message_id: int) -> None: ...


class ReplierPort(Protocol):
    async def reply(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: Any | None = None,
        reply_to_message_id: int | None = None,
    ) -> SentMessageRef | None: ...

    async def alert(self, text: str, *, modal: bool = True) -> None:
        """Короткий статус инициатору (в callback-контексте: alert либо toast)."""
        ...
