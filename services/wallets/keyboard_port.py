from typing import Any, Protocol


class WalletKeyboardPort(Protocol):
    def statements(self) -> Any: ...

    def remove_currency(self, *, code: str) -> Any: ...

    def undo(self, *, code: str, sign: str, amount: str) -> Any: ...


class NullWalletKeyboardPresenter(WalletKeyboardPort):
    def statements(self) -> None:
        return None

    def remove_currency(self, *, code: str) -> None:
        return None

    def undo(self, *, code: str, sign: str, amount: str) -> None:
        return None
