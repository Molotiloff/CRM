from __future__ import annotations

from decimal import Decimal

from services.client_balances.filter_service import ClientBalancesFilterService
from services.client_balances.query_service import ClientBalancesQueryService
from services.client_balances.report_builder import ClientBalancesReportBuilder


class DailyBalancesReportService:
    def __init__(
        self,
        *,
        query_service: ClientBalancesQueryService,
        filter_service: ClientBalancesFilterService,
        report_builder: ClientBalancesReportBuilder,
    ) -> None:
        self.query_service = query_service
        self.filter_service = filter_service
        self.report_builder = report_builder

    async def build_report(
        self,
        *,
        code_filter: str | None = None,
        sign_filter: str | None = None,
        min_negative_balance: Decimal | None = None,
        min_positive_balance: Decimal | None = None,
        excluded_client_group: str | None = None,
    ) -> list[str]:
        rows = await self.query_service.balances_by_client()

        if code_filter and sign_filter:
            normalized_code, normalized_sign, filtered = self.filter_service.filter_by_code_and_sign(
                rows,
                code_filter=code_filter,
                sign_filter=sign_filter,
                min_negative_balance=min_negative_balance,
                min_positive_balance=min_positive_balance,
                excluded_client_group=excluded_client_group,
            )
            return self.report_builder.build_signed_report(
                code_filter=normalized_code,
                sign_filter=normalized_sign,
                rows=filtered,
                min_negative_balance=min_negative_balance,
                min_positive_balance=min_positive_balance,
            )

        if code_filter:
            normalized_code, filtered = self.filter_service.filter_by_code(
                rows,
                code_filter=code_filter,
            )
            return self.report_builder.build_code_report(
                code_filter=normalized_code,
                rows=filtered,
                near_zero_threshold=self.filter_service.near_zero_threshold,
            )

        grouped = self.filter_service.group_nonzero_by_client(rows)
        return self.report_builder.build_full_report(grouped)
