from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class AppliedExchangeMovement:
    transaction_id: int
    currency_code: str
    direction: str  # IN / OUT relative to request chat stock
    amount: Decimal
