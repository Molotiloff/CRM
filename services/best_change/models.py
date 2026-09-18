from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum

from domain import DomainValidationError


class BestChangeOperation(StrEnum):
    PURCHASE = "purchase"
    SALE = "sale"


class BestChangeAccount(StrEnum):
    PURCHASE_TYM = "purchase_tym"
    SALE_TYM = "sale_tym"
    PURCHASE_CHLB = "purchase_chlb"
    SALE_CHLB = "sale_chlb"
    PLATFORM_FEE = "platform_fee"


class BestChangePaymentKind(StrEnum):
    PARTNER = "partner"
    COINDROP = "coindrop"


_CITY_ALIASES = {
    "тюм": "тюм",
    "тюмень": "тюм",
    "члб": "члб",
    "челябинск": "члб",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordBestChangeDeal:
    operation: BestChangeOperation | str
    city: str
    qty_usdt: Decimal
    market_rate_rub: Decimal
    client_rate_rub: Decimal
    actor_tg_user_id: int
    chat_id: int
    message_id: int
    deal_at: date
    comment: str | None = None

    def __post_init__(self) -> None:
        operation, city, qty, market_rate, client_rate = _normalize_deal_values(
            operation=self.operation,
            city=self.city,
            qty_usdt=self.qty_usdt,
            market_rate_rub=self.market_rate_rub,
            client_rate_rub=self.client_rate_rub,
        )
        if self.actor_tg_user_id <= 0:
            raise DomainValidationError("BestChange actor is required")
        if self.chat_id == 0 or self.message_id <= 0:
            raise DomainValidationError("BestChange Telegram message reference is invalid")
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "city", city)
        object.__setattr__(self, "qty_usdt", qty)
        object.__setattr__(self, "market_rate_rub", market_rate)
        object.__setattr__(self, "client_rate_rub", client_rate)

    @property
    def source_ref(self) -> str:
        return f"{self.chat_id}:{self.message_id}"


@dataclass(frozen=True, slots=True, kw_only=True)
class BestChangeCalculation:
    operation: BestChangeOperation
    city: str
    qty_usdt: Decimal
    market_rate_rub: Decimal
    client_rate_rub: Decimal
    unit_spread_rub: Decimal
    gross_spread_rub: Decimal
    platform_fee_rub_equivalent: Decimal
    platform_fee_usdt: Decimal
    profit_pool_rub: Decimal
    partner_share_rub: Decimal
    skyex_profit_rub: Decimal
    profit_account: BestChangeAccount

    @property
    def is_loss(self) -> bool:
        return self.gross_spread_rub < 0

    def body(self) -> dict[str, str]:
        return {
            "operation_kind": self.operation.value,
            "qty_usdt": str(self.qty_usdt),
            "market_rate_rub": str(self.market_rate_rub),
            "client_rate_rub": str(self.client_rate_rub),
            "unit_spread_rub": str(self.unit_spread_rub),
            "gross_spread_rub": str(self.gross_spread_rub),
            "platform_fee_rub_equivalent": str(self.platform_fee_rub_equivalent),
            "platform_fee_usdt": str(self.platform_fee_usdt),
            "profit_pool_rub": str(self.profit_pool_rub),
            "partner_share_rub": str(self.partner_share_rub),
            "skyex_profit_rub": str(self.skyex_profit_rub),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class BestChangePostingResult:
    deal_id: int
    calculation: BestChangeCalculation
    profit_balance_rub: Decimal
    platform_fee_balance_usdt: Decimal
    repeated: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class BestChangeMonthReport:
    period_month: date
    purchase_tym_rub: Decimal
    sale_tym_rub: Decimal
    purchase_chlb_rub: Decimal
    sale_chlb_rub: Decimal
    profit_pool_rub: Decimal
    partner_share_rub: Decimal
    partner_paid_rub: Decimal
    partner_delta_rub: Decimal
    skyex_profit_rub: Decimal
    platform_fee_accrued_usdt: Decimal
    platform_fee_paid_usdt: Decimal
    platform_fee_delta_usdt: Decimal
    platform_fee_outstanding_usdt: Decimal
    deal_ids: tuple[int, ...]
    closure_id: int | None = None
    closed_at: datetime | None = None
    closed_by_tg_user_id: int | None = None

    @property
    def deal_count(self) -> int:
        return len(self.deal_ids)


@dataclass(frozen=True, slots=True, kw_only=True)
class CloseBestChangeMonth:
    period_month: date
    actor_tg_user_id: int
    chat_id: int
    message_id: int
    comment: str | None = None

    def __post_init__(self) -> None:
        if self.period_month.day != 1:
            raise DomainValidationError("BestChange period must be the first day of a month")
        if self.actor_tg_user_id <= 0:
            raise DomainValidationError("BestChange close actor is required")
        if self.chat_id == 0 or self.message_id <= 0:
            raise DomainValidationError("BestChange close Telegram reference is invalid")

    @property
    def idempotency_key(self) -> str:
        return f"best-change:close:{self.chat_id}:{self.message_id}"


@dataclass(frozen=True, slots=True, kw_only=True)
class BestChangeMonthCloseResult:
    report: BestChangeMonthReport
    repeated: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordBestChangePayment:
    period_month: date
    kind: BestChangePaymentKind | str
    amount: Decimal
    payment_reference: str
    actor_tg_user_id: int
    chat_id: int
    message_id: int
    comment: str | None = None

    def __post_init__(self) -> None:
        if self.period_month.day != 1:
            raise DomainValidationError("BestChange period must be the first day of a month")
        try:
            kind = BestChangePaymentKind(self.kind)
        except ValueError:
            raise DomainValidationError(
                "BestChange payment kind must be partner or coindrop"
            ) from None
        precision = (
            Decimal("0.01")
            if kind is BestChangePaymentKind.PARTNER
            else Decimal("0.000001")
        )
        amount = _positive_decimal(self.amount, "BestChange payment amount").quantize(
            precision,
            rounding=ROUND_HALF_UP,
        )
        if amount == 0:
            raise DomainValidationError(
                "BestChange payment amount is below currency precision"
            )
        payment_reference = self.payment_reference.strip()
        if not payment_reference:
            raise DomainValidationError("BestChange payment reference is required")
        if self.actor_tg_user_id <= 0:
            raise DomainValidationError("BestChange payment actor is required")
        if self.chat_id == 0 or self.message_id <= 0:
            raise DomainValidationError("BestChange payment Telegram reference is invalid")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "payment_reference", payment_reference)

    @property
    def currency(self) -> str:
        return "RUB" if self.kind is BestChangePaymentKind.PARTNER else "USDT"

    @property
    def source_ref(self) -> str:
        return f"{self.chat_id}:{self.message_id}"

    @property
    def idempotency_key(self) -> str:
        return f"best-change:payment:{self.source_ref}"


@dataclass(frozen=True, slots=True, kw_only=True)
class BestChangePaymentResult:
    payment_id: int
    kind: BestChangePaymentKind
    amount: Decimal
    currency: str
    payment_reference: str
    report: BestChangeMonthReport
    repeated: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ReverseBestChangePayment:
    payment_id: int
    actor_tg_user_id: int
    chat_id: int
    message_id: int
    reason: str

    def __post_init__(self) -> None:
        if self.payment_id <= 0:
            raise DomainValidationError("BestChange payment id must be positive")
        _validate_reversal_reference(
            actor_tg_user_id=self.actor_tg_user_id,
            chat_id=self.chat_id,
            message_id=self.message_id,
            reason=self.reason,
        )
        object.__setattr__(self, "reason", self.reason.strip())

    @property
    def source_ref(self) -> str:
        return f"{self.chat_id}:{self.message_id}"

    @property
    def idempotency_key(self) -> str:
        return f"best-change:payment-reversal:{self.source_ref}"


@dataclass(frozen=True, slots=True, kw_only=True)
class BestChangePaymentReversalResult:
    original_payment_id: int
    reversal_payment_id: int
    report: BestChangeMonthReport
    repeated: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ReverseBestChangeMonth:
    period_month: date
    actor_tg_user_id: int
    chat_id: int
    message_id: int
    reason: str

    def __post_init__(self) -> None:
        if self.period_month.day != 1:
            raise DomainValidationError("BestChange period must be the first day of a month")
        _validate_reversal_reference(
            actor_tg_user_id=self.actor_tg_user_id,
            chat_id=self.chat_id,
            message_id=self.message_id,
            reason=self.reason,
        )
        object.__setattr__(self, "reason", self.reason.strip())

    @property
    def source_ref(self) -> str:
        return f"{self.chat_id}:{self.message_id}"

    @property
    def idempotency_key(self) -> str:
        return f"best-change:close-reversal:{self.source_ref}"


@dataclass(frozen=True, slots=True, kw_only=True)
class BestChangeMonthReversalResult:
    period_month: date
    original_closure_id: int
    reversal_closure_id: int
    repeated: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class CorrectBestChangeDeal:
    deal_id: int
    operation: BestChangeOperation | str
    city: str
    qty_usdt: Decimal
    market_rate_rub: Decimal
    client_rate_rub: Decimal
    actor_tg_user_id: int
    chat_id: int
    message_id: int
    reason: str

    def __post_init__(self) -> None:
        if self.deal_id <= 0:
            raise DomainValidationError("BestChange deal id must be positive")
        operation, city, qty, market_rate, client_rate = _normalize_deal_values(
            operation=self.operation,
            city=self.city,
            qty_usdt=self.qty_usdt,
            market_rate_rub=self.market_rate_rub,
            client_rate_rub=self.client_rate_rub,
        )
        _validate_reversal_reference(
            actor_tg_user_id=self.actor_tg_user_id,
            chat_id=self.chat_id,
            message_id=self.message_id,
            reason=self.reason,
        )
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "city", city)
        object.__setattr__(self, "qty_usdt", qty)
        object.__setattr__(self, "market_rate_rub", market_rate)
        object.__setattr__(self, "client_rate_rub", client_rate)
        object.__setattr__(self, "reason", self.reason.strip())

    @property
    def source_ref(self) -> str:
        return f"{self.chat_id}:{self.message_id}"

    @property
    def idempotency_key(self) -> str:
        return f"best-change:correction:{self.source_ref}"

    def replacement(self, *, deal_at: date) -> RecordBestChangeDeal:
        return RecordBestChangeDeal(
            operation=self.operation,
            city=self.city,
            qty_usdt=self.qty_usdt,
            market_rate_rub=self.market_rate_rub,
            client_rate_rub=self.client_rate_rub,
            actor_tg_user_id=self.actor_tg_user_id,
            chat_id=self.chat_id,
            message_id=self.message_id,
            deal_at=deal_at,
            comment=self.reason,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class BestChangeCorrectionResult:
    original_deal_id: int
    replacement: BestChangePostingResult
    repeated: bool


def _positive_decimal(value: object, field: str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise DomainValidationError(f"Invalid {field}") from None
    if not parsed.is_finite() or parsed <= 0:
        raise DomainValidationError(f"{field} must be positive")
    return parsed


def _normalize_deal_values(
    *,
    operation: BestChangeOperation | str,
    city: str,
    qty_usdt: Decimal,
    market_rate_rub: Decimal,
    client_rate_rub: Decimal,
) -> tuple[BestChangeOperation, str, Decimal, Decimal, Decimal]:
    try:
        normalized_operation = BestChangeOperation(operation)
    except ValueError:
        raise DomainValidationError(
            "BestChange operation must be purchase or sale"
        ) from None
    normalized_city = _CITY_ALIASES.get(city.strip().lower())
    if normalized_city is None:
        raise DomainValidationError("BestChange city must be тюм or члб")
    return (
        normalized_operation,
        normalized_city,
        _positive_decimal(qty_usdt, "BestChange quantity"),
        _positive_decimal(market_rate_rub, "BestChange market rate"),
        _positive_decimal(client_rate_rub, "BestChange client rate"),
    )


def _validate_reversal_reference(
    *,
    actor_tg_user_id: int,
    chat_id: int,
    message_id: int,
    reason: str,
) -> None:
    if actor_tg_user_id <= 0:
        raise DomainValidationError("BestChange reversal actor is required")
    if chat_id == 0 or message_id <= 0:
        raise DomainValidationError("BestChange reversal Telegram reference is invalid")
    if not reason.strip():
        raise DomainValidationError("BestChange reversal reason is required")
