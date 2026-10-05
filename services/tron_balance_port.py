from decimal import Decimal
from typing import Protocol


class TronBalanceProvider(Protocol):
    async def get_usdt_balance(self, *, address: str) -> Decimal: ...
