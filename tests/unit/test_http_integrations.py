from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest
import requests

import services.xe_api as xe_api_module
from gutils.requests_sheet import _execute
from services.aml.aml_service import AMLService
from services.aml.getblock_client import GetBlockAMLClient
from services.http_policy import HttpRetryPolicy, HttpTimeoutPolicy
from services.payment_watch.tronscan_gateway import (
    TronscanGateway,
    TronscanSettings,
)
from services.xe_api import ConverterAPIError, ConverterAPIService


def test_http_policies_bound_timeouts_attempts_and_server_delay() -> None:
    timeout = HttpTimeoutPolicy(
        connect_seconds=2,
        read_seconds=3,
        write_seconds=4,
        pool_seconds=5,
    )
    retry = HttpRetryPolicy(
        max_attempts=3,
        backoff_base_seconds=2,
        max_delay_seconds=10,
    )

    assert timeout.as_requests() == (2, 3)
    assert timeout.as_httpx().connect == 2
    assert retry.should_retry(attempt=1, status_code=503) is True
    assert retry.should_retry(attempt=1, status_code=404) is False
    assert retry.should_retry(attempt=3, status_code=503) is False
    assert retry.delay_seconds(attempt=3) == 8
    assert retry.delay_seconds(attempt=1, retry_after="120") == 10


async def test_converter_retries_retryable_status(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses = iter((503, 200))
    requests_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests_count
        requests_count += 1
        status = next(statuses)
        payload = (
            {"error": {"message": "unavailable"}}
            if status == 503
            else {
                "from_currency": "USD",
                "to_currency": "RUB",
                "amount": "10",
                "rate": "80",
                "converted": "800",
                "final_amount": "800",
                "percent": None,
                "percent_mode": "%",
                "sign": -1,
            }
        )
        return httpx.Response(status, json=payload, request=request)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        xe_api_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    service = ConverterAPIService(
        base_url="https://converter.test",
        api_token="token",
        retry_policy=HttpRetryPolicy(
            max_attempts=2,
            backoff_base_seconds=0,
        ),
    )

    result = await service.convert_text(text="10 usd rub")

    assert result.final_amount == 800
    assert requests_count == 2


async def test_converter_does_not_retry_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    requests_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests_count
        requests_count += 1
        return httpx.Response(401, json={}, request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        xe_api_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    service = ConverterAPIService(
        base_url="https://converter.test",
        api_token="bad-token",
        retry_policy=HttpRetryPolicy(max_attempts=3, backoff_base_seconds=0),
    )

    with pytest.raises(ConverterAPIError, match="токен"):
        await service.convert_text(text="10 usd rub")

    assert requests_count == 1


async def test_tronscan_retries_transport_failure() -> None:
    requests_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests_count
        requests_count += 1
        if requests_count == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"confirmations": 2}, request=request)

    gateway = TronscanGateway(
        settings=TronscanSettings(base_url="https://tronscan.test"),
        retry_policy=HttpRetryPolicy(max_attempts=2, backoff_base_seconds=0),
    )
    gateway.MIN_REQUEST_INTERVAL_SECONDS = 0
    gateway._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=gateway.timeout,
    )

    try:
        assert await gateway.get_confirmations(tx_hash="hash") == 2
    finally:
        await gateway.aclose()

    assert requests_count == 2


def test_getblock_retries_connection_error_but_not_404() -> None:
    client = GetBlockAMLClient(
        identity="identity",
        password="password",
        retry_policy=HttpRetryPolicy(max_attempts=2, backoff_base_seconds=0),
    )
    success = requests.Response()
    success.status_code = 200
    success.url = "https://getblock.test/report"
    not_found = requests.Response()
    not_found.status_code = 404
    not_found.url = success.url
    not_found_error = requests.HTTPError(response=not_found)

    try:
        client._get = Mock(side_effect=(requests.ConnectionError("offline"), success))
        assert client._get_with_retry("/report", max_attempts=2, delay_seconds=0) is success
        assert client._get.call_count == 2

        client._get = Mock(side_effect=not_found_error)
        with pytest.raises(requests.HTTPError):
            client._get_with_retry("/missing", max_attempts=2, delay_seconds=0)
        assert client._get.call_count == 1
    finally:
        client.close()


def test_aml_service_closes_getblock_client_on_failure() -> None:
    client = Mock()
    service = AMLService(settings=Mock())
    service._build_client = Mock(return_value=client)
    service._check_wallet = Mock(side_effect=RuntimeError("failed"))

    with pytest.raises(RuntimeError, match="failed"):
        service.check_wallet("wallet")

    client.close.assert_called_once_with()


def test_google_api_execute_uses_bounded_retries() -> None:
    request = Mock()
    request.execute.return_value = {"values": []}

    assert _execute(request) == {"values": []}
    request.execute.assert_called_once_with(num_retries=3)
