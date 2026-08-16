from typing import Any, Protocol


class CashKeyboardPort(Protocol):
    def deal_actions(self, *, request_id: str) -> Any: ...


class NullCashKeyboardPresenter(CashKeyboardPort):
    def deal_actions(self, *, request_id: str) -> None:
        return None
