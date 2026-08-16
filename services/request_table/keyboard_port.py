from __future__ import annotations

from typing import Any, Protocol


class RequestTableKeyboardPort(Protocol):
    def processing(self) -> Any | None: ...


class NullRequestTableKeyboardPresenter:
    def processing(self) -> None:
        return None
