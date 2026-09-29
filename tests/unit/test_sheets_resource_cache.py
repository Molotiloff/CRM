from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from gutils import requests_sheet


def test_nested_sheets_resources_are_reused_in_one_thread(monkeypatch) -> None:
    monkeypatch.setattr(requests_sheet, "_thread_cache", threading.local())
    service = MagicMock()

    assert requests_sheet._values(service) is requests_sheet._values(service)
    assert requests_sheet._spreadsheets(service) is requests_sheet._spreadsheets(service)
    service.spreadsheets.assert_called_once_with()
    service.spreadsheets.return_value.values.assert_called_once_with()


def test_sheets_service_and_resources_are_isolated_by_thread(monkeypatch) -> None:
    monkeypatch.setattr(requests_sheet, "_thread_cache", threading.local())
    monkeypatch.setattr(requests_sheet, "_get_credentials", lambda: object())
    monkeypatch.setattr(requests_sheet.httplib2, "Http", lambda **_: object())
    monkeypatch.setattr(requests_sheet, "AuthorizedHttp", lambda *_args, **_kwargs: object())
    services: list[MagicMock] = []

    def build(*_args, **_kwargs):
        service = MagicMock()
        services.append(service)
        return service

    monkeypatch.setattr(requests_sheet, "build", build)
    barrier = threading.Barrier(2)

    def worker():
        service = requests_sheet._get_service()
        values = requests_sheet._values(service)
        barrier.wait(timeout=5)
        assert requests_sheet._get_service() is service
        assert requests_sheet._values(service) is values
        return service

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker)
        second = pool.submit(worker)
        service_a, service_b = first.result(), second.result()

    assert service_a is not service_b
    assert len(services) == 2
    for service in services:
        service.spreadsheets.assert_called_once_with()
        service.spreadsheets.return_value.values.assert_called_once_with()
