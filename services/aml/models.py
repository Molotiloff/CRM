from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AMLNetwork = Literal["trc20", "erc20", "bep20", "btc"]
AMLKind = Literal["address", "transaction"]


@dataclass(frozen=True, slots=True)
class AMLCheckRequest:
    value: str
    network: AMLNetwork = "trc20"
    kind: AMLKind = "address"

    @property
    def currency_code(self) -> str:
        return {"trc20": "TRX", "erc20": "ETH", "bep20": "BSC", "btc": "BTC"}[self.network]

    @property
    def token_id(self) -> str:
        return {"trc20": "9", "erc20": "94252", "bep20": "9", "btc": ""}[self.network]
