from __future__ import annotations

from decimal import Decimal

from services.client_balances import ScheduledBalancesReportService
from tests.fakes import FakeMessenger


class ReportServiceStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, Decimal | None]] = []

    async def build_report(
        self,
        *,
        code_filter: str | None = None,
        sign_filter: str | None = None,
        min_negative_balance: Decimal | None = None,
        min_positive_balance: Decimal | None = None,
        excluded_client_group: str | None = None,
    ) -> list[str]:
        self.calls.append((code_filter, min_negative_balance))
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
    assert [item.text for item in messenger.sent_to(10)] == [
        "📊 <b>Ежедневный отчёт по балансам</b>",
        "RUB debt report",
    ]
    assert [item.text for item in messenger.sent_to(20)] == [
        "📊 <b>Ежедневный отчёт по балансам</b>",
        "RUB debt report",
    ]
