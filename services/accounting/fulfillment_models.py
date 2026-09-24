from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class FulfillmentRequestKind(StrEnum):
    SALE = "sale"
    # Legacy persisted name: /отпр may now settle in either transfer direction.
    CLIENT_WITHDRAWAL = "client_withdrawal"


class FulfillmentStatus(StrEnum):
    QUEUED = "queued"
    EXECUTING = "executing"
    COMPLETED = "completed"
    CANCELED = "canceled"


@dataclass(frozen=True, slots=True, kw_only=True)
class FulfillmentQueueItem:
    id: int
    deal_id: int
    request_kind: FulfillmentRequestKind
    qty: Decimal
    sequence_no: int
    status: FulfillmentStatus
    created_by: int | None
    created_at: datetime
    reordered_by: int | None
    reordered_at: datetime | None
    payment_watch_id: int | None = None
    payment_event_id: int | None = None
    position_move_id: int | None = None
    wallet_source: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class FulfillmentQueueSummary:
    queued_qty: Decimal
    usdt_fact: Decimal | None
    queue_shortage_qty: Decimal | None
    onchain_liquid_qty: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientWithdrawalContext:
    client_id: int
    qty: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class EnqueueFulfillment:
    deal_id: int
    request_kind: FulfillmentRequestKind
    qty: Decimal
    actor_user_id: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReorderFulfillment:
    item_id: int
    before_item_id: int | None
    actor_user_id: int


@dataclass(frozen=True, slots=True, kw_only=True)
class StartFulfillmentExecution:
    chat_id: int
    chat_name: str | None
    reply_message_id: int
    address: str
    our_address: str
    actor_tg_user_id: int | None
    requested_qty: Decimal | None
    mode: str
    phase: str
    timeout_at: datetime
    command_message_id: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class FulfillmentExecutionStarted:
    item: FulfillmentQueueItem
    watch_id: int
