from __future__ import annotations

from api.presentation.balances import build_balances_snapshot
from api.read_repositories import BalanceReadRepository
from api.schemas.balances import BalancesSnapshotResponse


class BalanceQueryService:
    def __init__(self, repository: BalanceReadRepository) -> None:
        self._repository = repository

    async def get_snapshot(
        self,
        *,
        currency: str | None = None,
        sign: str | None = None,
    ) -> BalancesSnapshotResponse:
        rows = await self._repository.nonzero_balances(currency=currency, sign=sign)
        rates = await self._repository.latest_rub_rates()
        return build_balances_snapshot(rows=rows, rub_rates=rates)
