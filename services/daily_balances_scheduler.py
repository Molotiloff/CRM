from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from services.client_balances import ScheduledBalancesReportService


def setup_daily_balances_scheduler(
    *,
    report_service: ScheduledBalancesReportService,
    timezone: str = "Asia/Yekaterinburg",
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=timezone)

    scheduler.add_job(
        report_service.send_negative_balances,
        trigger=CronTrigger(hour=18, minute=0),
        kwargs={"currencies": ("RUB", "USDT")},
        id="daily_negative_balances",
        replace_existing=True,
    )

    return scheduler
