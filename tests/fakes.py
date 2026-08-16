"""Фейковые реализации портов messaging для тестов use-cases (C0.3)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.messaging import MessengerError, MessengerPort, SentMessageRef


@dataclass(slots=True, frozen=True)
class SentRecord:
    chat_id: int
    message_id: int
    text: str
    reply_markup: Any | None
    reply_to_message_id: int | None


@dataclass(slots=True, frozen=True)
class EditRecord:
    chat_id: int
    message_id: int
    text: str | None  # None — правка только клавиатуры
    reply_markup: Any | None
    preserve_reply_markup: bool = False


@dataclass(slots=True, frozen=True)
class DeleteRecord:
    chat_id: int
    message_id: int


@dataclass
class FakeMessenger(MessengerPort):
    """Записывает send/edit; message_id растут монотонно от 1000.

    fail_chats — чаты, отправка/правка в которые падает MessengerError
    (benign_failures управляет флагом benign).
    """

    fail_chats: set[int] = field(default_factory=set)
    benign_failures: bool = False
    sent: list[SentRecord] = field(default_factory=list)
    edits: list[EditRecord] = field(default_factory=list)
    deletes: list[DeleteRecord] = field(default_factory=list)
    _next_id: int = 1000

    def _check(self, chat_id: int) -> None:
        if chat_id in self.fail_chats:
            raise MessengerError(f"fake failure for chat {chat_id}", benign=self.benign_failures)

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
        reply_to_message_id: int | None = None,
        disable_notification: bool = False,
    ) -> SentMessageRef:
        self._check(chat_id)
        self._next_id += 1
        self.sent.append(
            SentRecord(
                chat_id=chat_id,
                message_id=self._next_id,
                text=text,
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
            )
        )
        return SentMessageRef(chat_id=chat_id, message_id=self._next_id, chat_title=f"chat:{chat_id}")

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
        self._check(chat_id)
        self._next_id += 1
        self.sent.append(
            SentRecord(
                chat_id=chat_id,
                message_id=self._next_id,
                text=caption or "",
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
            )
        )
        return SentMessageRef(chat_id=chat_id, message_id=self._next_id, chat_title=f"chat:{chat_id}")

    async def edit_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
        preserve_reply_markup: bool = False,
    ) -> None:
        self._check(chat_id)
        self.edits.append(
            EditRecord(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                preserve_reply_markup=preserve_reply_markup,
            )
        )

    async def edit_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: Any | None = None,
    ) -> None:
        self._check(chat_id)
        self.edits.append(
            EditRecord(chat_id=chat_id, message_id=message_id, text=None, reply_markup=reply_markup)
        )

    async def edit_caption(
        self,
        chat_id: int,
        message_id: int,
        caption: str,
        *,
        reply_markup: Any | None = None,
        preserve_reply_markup: bool = False,
    ) -> None:
        self._check(chat_id)
        self.edits.append(
            EditRecord(
                chat_id=chat_id,
                message_id=message_id,
                text=caption,
                reply_markup=reply_markup,
                preserve_reply_markup=preserve_reply_markup,
            )
        )

    async def delete(self, chat_id: int, message_id: int) -> None:
        self._check(chat_id)
        self.deletes.append(DeleteRecord(chat_id=chat_id, message_id=message_id))

    def sent_to(self, chat_id: int) -> list[SentRecord]:
        return [s for s in self.sent if s.chat_id == chat_id]


class FakeExchangeKeyboardPresenter:
    def client_cancel(self, **kwargs):
        return ("client_cancel", kwargs)

    def request_chat(self, **kwargs):
        return ("request_chat", kwargs)

    def table_create(self, **kwargs):
        return ("table_create", kwargs)

    def table_delete(self, **kwargs):
        return ("table_delete", kwargs)
