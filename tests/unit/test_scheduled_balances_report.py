from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from services.client_balances import ScheduledBalancesReportService
from services.client_balances.filter_service import ClientBalancesFilterService
from services.client_balances.query_service import ClientBalanceRow
from services.client_balances.scheduled_report_service import EXCLUDED_SCHEDULED_GROUPS
from tests.fakes import FakeMessenger


def test_scheduled_filter_excludes_internal_groups_but_manual_report_keeps_them() -> None:
    service = ClientBalancesFilterService()
    for code, threshold in (("RUB", Decimal("-1000")), ("USDT", Decimal("-1"))):
        rows = [
            ClientBalanceRow(
                client_id=index,
                client_name=f"Client {index}",
                chat_id=-index,
                client_group=group,
                currency_code=code,
                balance=threshold * 2,
                precision=2,
            )
            for index, group in enumerate(
                ("Балансы", "  бАлАнСы  ", "internal_wallet", " INTERNAL_WALLET ", "", "VIP"),
                start=1,
            )
        ]
        _, _, scheduled = service.filter_by_code_and_sign(
            rows,
            code_filter=code,
            sign_filter="-",
            min_negative_balance=threshold,
            excluded_client_groups=EXCLUDED_SCHEDULED_GROUPS,
        )
        assert [row.client_id for row in scheduled] == [5, 6]
        _, _, manual = service.filter_by_code_and_sign(rows, code_filter=code, sign_filter="-")
        assert len(manual) == 6
        _, _, legacy = service.filter_by_code_and_sign(
            rows,
            code_filter=code,
            sign_filter="-",
            excluded_client_group="Балансы",
        )
        assert [row.client_id for row in legacy] == [3, 4, 5, 6]


class ReportServiceStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, Decimal | None]] = []
        self.excluded_groups: list[tuple[str, ...]] = []

    async def build_report(
        self,
        *,
        code_filter: str | None = None,
        sign_filter: str | None = None,
        min_negative_balance: Decimal | None = None,
        min_positive_balance: Decimal | None = None,
        excluded_client_group: str | None = None,
        excluded_client_groups: Iterable[str] = (),
    ) -> list[str]:
        self.calls.append((code_filter, min_negative_balance))
        self.excluded_groups.append(tuple(excluded_client_groups))
        if code_filter == "RUB":
            return ["RUB debt report"]
        return ["Нет клиентов по выбранным условиям"]


async def test_scheduled_report_delivers_via_messenger_port() -> None:
    report_service = ReportServiceStub()
    messenger = FakeMessenger()
    service = ScheduledBalancesReportService(
        report_service=report_service,
        messenger=messenger,
        admin_chat_ids=[10, 20],
    )

    await service.send_negative_balances()

    assert report_service.calls == [
        ("RUB", Decimal("-1000")),
        ("USDT", Decimal("-1")),
        ("RUB", Decimal("-1000")),
        ("USDT", Decimal("-1")),
    ]
    assert report_service.excluded_groups == [("Балансы", "internal_wallet")] * 4
    assert [item.text for item in messenger.sent_to(10)] == [
        "📊 <b>Ежедневный отчёт по балансам</b>",
        "RUB debt report",
    ]
    assert [item.text for item in messenger.sent_to(20)] == [
        "📊 <b>Ежедневный отчёт по балансам</b>",
        "RUB debt report",
    ]
