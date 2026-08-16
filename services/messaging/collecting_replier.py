from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.messaging.ports import ReplierPort, SentMessageRef


@dataclass
class CollectingReplier(ReplierPort):
    replies: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    async def reply(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: Any | None = None,
        reply_to_message_id: int | None = None,
    ) -> SentMessageRef | None:
        self.replies.append(text)
        return None

    async def alert(self, text: str, *, modal: bool = True) -> None:
        self.alerts.append(text)

