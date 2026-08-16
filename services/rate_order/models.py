from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LiveMessageEditStatus(StrEnum):
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    MISSING = "missing"
    RETRY = "retry"
    FAILED = "failed"


@dataclass(slots=True, frozen=True)
class LiveMessageEditResult:
    status: LiveMessageEditStatus
    retry_after_seconds: float | None = None

