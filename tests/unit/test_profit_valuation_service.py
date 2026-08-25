from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from domain import DomainStateError, DomainValidationError
from services.accounting.models import ManualProfitQuote, MarketQuote
from services.accounting.profit_service import ProfitValuationService


class QuoteProvider:
    def __init__(self, quote: MarketQuote | None) -> None:
        self.quote = quote

    async def best_bid(self, symbol: str) -> MarketQuote | None:
        del symbol
        return self.quote


@pytest.mark.asyncio
async def test_profit_valuation_accepts_fresh_market_quote() -> None:
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    quote = MarketQuote(
        price=Decimal("91.25"),
        source="rapira_ws",
        symbol="USDT/RUB",
        observed_at=now - timedelta(seconds=30),
    )
    result, actor, reason = await ProfitValuationService(
        QuoteProvider(quote),
        now_factory=lambda: now,
    ).resolve("USDT/RUB")

    assert result == quote
    assert actor is None
    assert reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("quote", [None, "stale"])
async def test_profit_valuation_rejects_missing_or_stale_quote(quote) -> None:
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    value = (
        MarketQuote(
            price=Decimal("91"),
            source="rapira_ws",
            symbol="USDT/RUB",
            observed_at=now - timedelta(minutes=6),
        )
        if quote == "stale"
        else None
    )
    with pytest.raises(DomainStateError):
        await ProfitValuationService(
            QuoteProvider(value),
            now_factory=lambda: now,
        ).resolve("USDT/RUB")


@pytest.mark.asyncio
async def test_manual_profit_quote_requires_audit_reason() -> None:
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    service = ProfitValuationService(QuoteProvider(None), now_factory=lambda: now)

    with pytest.raises(DomainValidationError):
        await service.resolve(
            "USDT/RUB",
            manual=ManualProfitQuote(
                price=Decimal("92"),
                actor_user_id=10,
                reason=" ",
                observed_at=now,
            ),
        )

    quote, actor, reason = await service.resolve(
        "USDT/RUB",
        manual=ManualProfitQuote(
            price=Decimal("92"),
            actor_user_id=10,
            reason="Rapira unavailable",
            observed_at=now,
        ),
    )
    assert (quote.source, actor, reason) == ("manual", 10, "Rapira unavailable")
