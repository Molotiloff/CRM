from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .reporting import BestChangeMonthlyReportPublisher


def setup_best_change_month_report_scheduler(
    *,
    publisher: BestChangeMonthlyReportPublisher,
    timezone: str = "Asia/Yekaterinburg",
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        publisher.publish_previous_month,
        trigger=CronTrigger(day=1, hour=9, minute=0, timezone=timezone),
        id="best_change_preliminary_month_report",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return scheduler
