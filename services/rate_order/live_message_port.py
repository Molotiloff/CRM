from __future__ import annotations

from typing import Protocol

from services.rate_order.models import LiveMessageEditResult


class LiveMessageEditorPort(Protocol):
    async def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> LiveMessageEditResult: ...

