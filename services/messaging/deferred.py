from __future__ import annotations

from typing import Any

from .ports import MessengerError, MessengerPort, SentMessageRef


class DeferredMessenger(MessengerPort):
    """Defers Telegram delivery to the transactional outbox.

    Existing-message operations are accepted without network I/O. Creating a
    new Telegram message is intentionally rejected so an HTTP mutation cannot
    silently persist a synthetic Telegram message id.
    """

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
        reply_to_message_id: int | None = None,
        disable_notification: bool = False,
    ) -> SentMessageRef:
        raise MessengerError("Telegram send is deferred to outbox", benign=True)

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
    ) -> SentMessageRef:
        raise MessengerError("Telegram send is deferred to outbox", benign=True)

    async def edit_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
        preserve_reply_markup: bool = False,
    ) -> None:
        return None

    async def edit_caption(
        self,
        chat_id: int,
        message_id: int,
        caption: str,
        *,
        reply_markup: Any | None = None,
        preserve_reply_markup: bool = False,
    ) -> None:
        return None

    async def edit_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: Any | None = None,
    ) -> None:
        return None

    async def delete(self, chat_id: int, message_id: int) -> None:
        return None
