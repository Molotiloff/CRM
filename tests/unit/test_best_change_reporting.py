from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.best_change import (
    BestChangeMonthlyReportPublisher,
    BestChangeMonthReport,
    format_month_report,
    previous_month,
    setup_best_change_month_report_scheduler,
)
from tests.fakes import FakeMessenger


def _report(*, closed: bool = False) -> BestChangeMonthReport:
    return BestChangeMonthReport(
        period_month=date(2026, 8, 1),
        purchase_tym_rub=Decimal("100"),
        sale_tym_rub=Decimal("200"),
        purchase_chlb_rub=Decimal("300"),
        sale_chlb_rub=Decimal("400"),
        profit_pool_rub=Decimal("1000"),
        partner_share_rub=Decimal("500"),
        partner_paid_rub=Decimal("100"),
        partner_delta_rub=Decimal("400"),
        skyex_profit_rub=Decimal("500"),
        platform_fee_accrued_usdt=Decimal("3.123456"),
        platform_fee_paid_usdt=Decimal("0"),
        platform_fee_delta_usdt=Decimal("3.123456"),
        platform_fee_outstanding_usdt=Decimal("5.123456"),
        deal_ids=(801, 802),
        closure_id=17 if closed else None,
        closed_at=(datetime(2026, 9, 1, 9, tzinfo=UTC) if closed else None),
        closed_by_tg_user_id=42 if closed else None,
    )


def test_previous_month_crosses_year_boundary() -> None:
    assert previous_month(date(2026, 1, 15)) == date(2025, 12, 1)


def test_format_closed_month_report_contains_audit_details() -> None:
    text = format_month_report(_report(closed=True))

    assert "BestChange закрыт · 08.2026 · #17" in text
    assert "Статус CoinDrop: не оплачено" in text
    assert "Выплачено партнёру: 100.00 RUB" in text
    assert "Остаток партнёру: 400.00 RUB" in text
    assert "CRM: #801, #802" in text
    assert "Закрыл: 42 · 01.09.2026 09:00" in text


@pytest.mark.asyncio
async def test_publisher_sends_preliminary_previous_month_report() -> None:
    service = SimpleNamespace(month_report=AsyncMock(return_value=_report()))
    messenger = FakeMessenger()
    publisher = BestChangeMonthlyReportPublisher(
        service=service,
        messenger=messenger,
        chat_id=-100500,
        today_factory=lambda: date(2026, 9, 1),
    )

    await publisher.publish_previous_month()

    service.month_report.assert_awaited_once_with(date(2026, 8, 1))
    assert messenger.sent[0].chat_id == -100500
    assert "Предварительная справка BestChange · 08.2026" in messenger.sent[0].text


def test_scheduler_registers_first_day_job() -> None:
    publisher = SimpleNamespace(publish_previous_month=AsyncMock())

    scheduler = setup_best_change_month_report_scheduler(publisher=publisher)
    job = scheduler.get_job("best_change_preliminary_month_report")

    assert job is not None
    assert str(job.trigger) == "cron[day='1', hour='9', minute='0']"
