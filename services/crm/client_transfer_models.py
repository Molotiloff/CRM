from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ClientTransferCommand:
    amount: Decimal
    currency: str
    source: str
    source_ref: str
    city: str
    from_chat_id: int | None = None
    from_client_id: int | None = None
    to_client_name: str | None = None
    to_client_id: int | None = None
    actor_user_id: int | None = None
    actor_tg_user_id: int | None = None
    comment: str | None = None
    allow_negative: bool = False


@dataclass(frozen=True, slots=True)
class ClientTransferResult:
    deal_id: int
    from_client_name: str
    to_client_name: str
    to_chat_id: int
    from_balance: Decimal
    to_balance: Decimal
    amount: Decimal
    currency: str
    precision: int
    created_at: datetime
    repeated: bool
