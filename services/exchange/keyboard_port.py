from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol


class ExchangeKeyboardPort(Protocol):
    def client_cancel(
        self,
        *,
        request_id: int | str,
        table_request_id: int | str | None,
    ) -> Any: ...

    def request_chat(
        self,
        *,
        request_id: int | str,
        table_request_id: int | str,
    ) -> Any: ...

    def table_create(
        self,
        *,
        receive_code: str,
        pay_code: str,
        receive_amount: Decimal,
        pay_amount: Decimal,
        rate: Decimal | str,
        table_request_id: int | str,
    ) -> Any: ...

    def table_delete(self, *, table_request_id: int | str) -> Any: ...
