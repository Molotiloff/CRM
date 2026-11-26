from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class WidgetBinding:
    chat_id: int
    message_id: int
    frame: int = 15
    tails: list[int] = field(default_factory=list)
    last_state: str | None = None
    last_caption: str | None = None
    last_tail_text: dict[int, str] = field(default_factory=dict)
    blocked_until: float = 0.0


class WidgetBindingPort(Protocol):
    async def load(self, *, limit: int) -> list[WidgetBinding]: ...
    async def save(self, binding: WidgetBinding) -> None: ...
    async def remove(self, chat_id: int) -> None: ...
