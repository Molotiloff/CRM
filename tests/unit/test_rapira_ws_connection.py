from __future__ import annotations

import asyncio

import pytest

from services.rate_order import rapira_ws_service


async def test_rapira_connection_disables_compression(monkeypatch: pytest.MonkeyPatch) -> None:
    options: dict[str, object] = {}

    class CancelledConnect:
        async def __aenter__(self):
            raise asyncio.CancelledError

        async def __aexit__(self, *_args):
            return False

    def fake_connect(_url: str, **kwargs):
        options.update(kwargs)
        return CancelledConnect()

    monkeypatch.setattr(rapira_ws_service.websockets, "connect", fake_connect)

    with pytest.raises(asyncio.CancelledError):
        await rapira_ws_service.RapiraWsService()._run()

    assert options["compression"] is None
    assert options["ping_interval"] is None
