from __future__ import annotations

from typing import Any

from .ports import MessengerPort, ReplierPort, SentMessageRef


class ClientChatReplier(ReplierPort):
    """Send use-case replies to a client chat when there is no Telegram command message."""

    def __init__(self, messenger: MessengerPort, chat_id: int) -> None:
        self._messenger = messenger
        self._chat_id = chat_id

    async def reply(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: Any | None = None,
        reply_to_message_id: int | None = None,
    ) -> SentMessageRef:
        del parse_mode  # MessengerPort sends HTML, as does AiogramMessageReplier here.
        return await self._messenger.send(
            self._chat_id,
            text,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )

    async def alert(self, text: str, *, modal: bool = True) -> None:
        del modal
        await self.reply(text)
