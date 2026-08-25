from __future__ import annotations

from services.accounting.models import MarketQuote

from .rapira_ws_service import RapiraWsService


class RapiraMarketQuoteProvider:
    """Application-facing best-bid view over the existing Rapira snapshot."""

    def __init__(self, service: RapiraWsService) -> None:
        self._service = service

    async def best_bid(self, symbol: str) -> MarketQuote | None:
        if symbol != "USDT/RUB":
            return None
        price = self._service.get_best_bid()
        observed_at = self._service.orderbook_observed_at
        if price is None or observed_at is None:
            return None
        return MarketQuote(
            price=price,
            source="rapira_ws",
            symbol=symbol,
            observed_at=observed_at,
        )
