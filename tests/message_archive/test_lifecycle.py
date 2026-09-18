from __future__ import annotations

import asyncio

from services.message_archive.lifecycle import MessageArchiveLifecycle


class _Component:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def start(self) -> None:
        self.calls.append(f"start:{self.name}")

    async def stop(self) -> None:
        self.calls.append(f"stop:{self.name}")


def test_archive_lifecycle_starts_workers_and_stops_in_safe_order() -> None:
    calls: list[str] = []
    lifecycle = MessageArchiveLifecycle(
        media=_Component("media", calls),  # type: ignore[arg-type]
        monitor=_Component("monitor", calls),  # type: ignore[arg-type]
        exports=_Component("exports", calls),
    )

    async def scenario() -> None:
        await lifecycle.start()
        await lifecycle.stop()

    asyncio.run(scenario())

    assert calls == [
        "start:media",
        "start:monitor",
        "stop:exports",
        "stop:monitor",
        "stop:media",
    ]
