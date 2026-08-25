from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from domain import DomainStateError, DomainValidationError
from services.unit_of_work import UnitOfWorkFactory

from .firm_position_service import FirmPositionAccountingService
from .models import (
    AccrueUsdtProfit,
    ManualProfitQuote,
    MarketQuote,
    ProfitAccrual,
    ProfitCapitalizationResult,
    RecordProfitCapitalization,
)
from .ports import MarketQuoteProviderPort


class ProfitValuationService:
    def __init__(
        self,
        quote_provider: MarketQuoteProviderPort,
        *,
        max_quote_age: timedelta = timedelta(minutes=5),
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._quote_provider = quote_provider
        self._max_quote_age = max_quote_age
        self._now = now_factory or (lambda: datetime.now(UTC))

    async def resolve(
        self,
        symbol: str,
        *,
        manual: ManualProfitQuote | None = None,
    ) -> tuple[MarketQuote, int | None, str | None]:
        if manual is not None:
            price = self._positive(manual.price, "manual valuation price")
            reason = manual.reason.strip()
            if manual.actor_user_id <= 0:
                raise DomainValidationError("Manual valuation actor is required")
            if not reason:
                raise DomainValidationError("Manual valuation reason is required")
            if manual.observed_at.tzinfo is None:
                raise DomainValidationError("Manual valuation time must be timezone-aware")
            return (
                MarketQuote(
                    price=price,
                    source="manual",
                    symbol=symbol,
                    observed_at=manual.observed_at,
                ),
                manual.actor_user_id,
                reason,
            )
        quote = await self._quote_provider.best_bid(symbol)
        if quote is None:
            raise DomainStateError("Market quote is unavailable")
        if quote.symbol != symbol:
            raise DomainStateError("Market quote symbol does not match request")
        if quote.observed_at.tzinfo is None:
            raise DomainStateError("Market quote time must be timezone-aware")
        age = self._now() - quote.observed_at
        if age < timedelta(0) or age > self._max_quote_age:
            raise DomainStateError("Market quote is stale")
        self._positive(quote.price, "market quote price")
        return quote, None, None

    @staticmethod
    def _positive(value: object, field: str) -> Decimal:
        try:
            parsed = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise DomainValidationError(f"Invalid {field}") from None
        if not parsed.is_finite() or parsed <= 0:
            raise DomainValidationError(f"{field.capitalize()} must be positive")
        return parsed


class ProfitAccrualService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def accrue(self, command: AccrueUsdtProfit) -> ProfitAccrual:
        qty = ProfitValuationService._positive(command.qty, "profit quantity")
        key = command.idempotency_key.strip()
        if not key:
            raise DomainValidationError("Profit idempotency key is required")
        async with self._unit_of_work_factory() as unit_of_work:
            existing = await unit_of_work.profit_accruals.get_by_idempotency_key(key)
            if existing is not None:
                if (
                    existing.deal_id != command.deal_id
                    or existing.qty != qty
                    or existing.settlement_status is not command.settlement_status
                ):
                    raise DomainStateError("Idempotency key belongs to another profit accrual")
                return existing
            deal = await unit_of_work.deals.get_deal(command.deal_id)
            if deal is None:
                raise DomainValidationError("Profit deal was not found")
            result = await unit_of_work.profit_accruals.append(
                deal_id=command.deal_id,
                qty=qty,
                settlement_status=command.settlement_status.value,
                idempotency_key=key,
            )
            await unit_of_work.commit()
            return result

    async def mark_received(
        self,
        *,
        accrual_id: int,
        payment_event_id: int,
        received_at: datetime,
    ) -> ProfitAccrual:
        if received_at.tzinfo is None:
            raise DomainValidationError("Profit receipt time must be timezone-aware")
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.profit_accruals.mark_received(
                accrual_id=accrual_id,
                payment_event_id=payment_event_id,
                received_at=received_at,
            )
            await unit_of_work.commit()
            return result


class MidnightProfitCapitalizationJob:
    SYMBOL = "USDT/RUB"

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        valuation_service: ProfitValuationService,
        position_service: FirmPositionAccountingService,
        timezone: str = "Asia/Yekaterinburg",
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._valuation_service = valuation_service
        self._position_service = position_service
        self._timezone = ZoneInfo(timezone)
        self._now = now_factory or (lambda: datetime.now(UTC))

    async def run_previous_day(self) -> ProfitCapitalizationResult:
        business_date = self._now().astimezone(self._timezone).date() - timedelta(days=1)
        return await self.run(business_date)

    async def run(
        self,
        business_date: date,
        *,
        manual_quote: ManualProfitQuote | None = None,
    ) -> ProfitCapitalizationResult:
        boundary = datetime.combine(
            business_date + timedelta(days=1),
            time.min,
            tzinfo=self._timezone,
        )
        key = f"profit-capitalization:{business_date.isoformat()}"
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.profit_accruals.acquire_capitalization_lock(business_date)
            existing_move = await unit_of_work.firm_positions.get_by_idempotency_key(key)
            if existing_move is not None:
                return ProfitCapitalizationResult(
                    business_date=business_date,
                    qty=existing_move.qty,
                    rub_value=existing_move.rub_amount,
                    accrual_count=0,
                    position_move_id=existing_move.id,
                    repeated=True,
                )
            accruals = await unit_of_work.profit_accruals.list_pending_before(boundary)
            if not accruals:
                await unit_of_work.commit()
                return ProfitCapitalizationResult(
                    business_date=business_date,
                    qty=Decimal(0),
                    rub_value=Decimal(0),
                    accrual_count=0,
                    position_move_id=None,
                    repeated=False,
                )
            quote, actor_user_id, reason = await self._valuation_service.resolve(
                self.SYMBOL,
                manual=manual_quote,
            )
            qty = sum((item.qty for item in accruals), Decimal(0))
            capitalized_at = self._now()
            move = await self._position_service.record_profit_capitalization(
                RecordProfitCapitalization(
                    currency="USDT",
                    qty=qty,
                    rate=quote.price,
                    deal_id=None,
                    actor_user_id=actor_user_id,
                    effective_at=boundary,
                    idempotency_key=key,
                ),
                unit_of_work=unit_of_work,
            )
            await unit_of_work.profit_accruals.mark_capitalized(
                accrual_ids=tuple(item.id for item in accruals),
                quote=quote,
                move_id=move.id,
                capitalized_at=capitalized_at,
                actor_user_id=actor_user_id,
                reason=reason,
            )
            await unit_of_work.commit()
            return ProfitCapitalizationResult(
                business_date=business_date,
                qty=qty,
                rub_value=qty * quote.price,
                accrual_count=len(accruals),
                position_move_id=move.id,
                repeated=False,
            )
