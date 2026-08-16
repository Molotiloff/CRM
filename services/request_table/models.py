from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True, frozen=True)
class RequestTableCallbackCommand:
    chat_id: int
    message_id: int
    message_text: str
    message_dt: datetime | None
    callback_data: str
