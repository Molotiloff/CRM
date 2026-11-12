from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class WidgetBinding:
    chat_id: int
    message_id: int
    frame: int = 15
    tails: list[int] = field(default_factory=list)


class WidgetBindingPort(Protocol):
    async def load(self, *, limit: int) -> list[WidgetBinding]: ...
    async def save(self, binding: WidgetBinding) -> None: ...
    async def remove(self, chat_id: int) -> None: ...
