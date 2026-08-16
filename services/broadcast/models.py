from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BroadcastDeliveryStatus(StrEnum):
    SENT = "sent"
    BLOCKED = "blocked"
    NOT_FOUND = "not_found"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(slots=True, frozen=True)
class BroadcastCommand:
    target_group: str | None = None


@dataclass(slots=True, frozen=True)
class BroadcastResult:
    sent: int = 0
    blocked: int = 0
    not_found: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def attempted(self) -> int:
        return self.sent + self.blocked + self.not_found + self.failed

