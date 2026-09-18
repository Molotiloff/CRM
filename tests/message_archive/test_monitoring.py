from __future__ import annotations

import asyncio

from services.message_archive.monitoring import (
    ArchiveRuntimeMetrics,
    MessageArchiveMonitor,
)


class _Repo:
    async def get_archive_statistics(self):
        return {
            "chats": 2,
            "messages": 12,
            "media_pending": 1,
            "media_failed": 0,
        }


class _MediaQueue:
    queue_size = 3
    active_jobs = 4


def test_runtime_metrics_separate_saved_results_and_failure_directions() -> None:
    metrics = ArchiveRuntimeMetrics()
    metrics.record_saved("created")
    metrics.record_saved("updated")
    metrics.record_saved("skipped")
    metrics.record_failure("inbound")
    metrics.record_failure("outbound")
    metrics.record_failure("imported")

    assert metrics.created == 1
    assert metrics.updated == 1
    assert metrics.skipped == 1
    assert metrics.failures == 3
    assert metrics.inbound_failures == 1
    assert metrics.outbound_failures == 1
    assert metrics.import_failures == 1


def test_monitor_combines_database_and_runtime_snapshot() -> None:
    metrics = ArchiveRuntimeMetrics(created=5, failures=2)
    monitor = MessageArchiveMonitor(
        repo=_Repo(),
        runtime=metrics,
        media_queue=_MediaQueue(),
    )

    snapshot = asyncio.run(monitor.log_snapshot())

    assert snapshot["messages"] == 12
    assert snapshot["queue_size"] == 3
    assert snapshot["active_media_jobs"] == 4
    assert snapshot["runtime_created"] == 5
    assert snapshot["runtime_failures"] == 2

