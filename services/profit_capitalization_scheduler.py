from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from services.accounting.profit_service import MidnightProfitCapitalizationJob


def setup_profit_capitalization_scheduler(
    *,
    job: MidnightProfitCapitalizationJob,
    timezone: str = "Asia/Yekaterinburg",
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        job.run_previous_day,
        trigger=CronTrigger(hour=0, minute=0, timezone=timezone),
        id="midnight_profit_capitalization",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return scheduler
