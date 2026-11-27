from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from handlers.aml import AMLHandler
from services.aml.aml_service import AMLService
from services.aml.getblock_client import GetBlockAMLClient
from services.aml.getblock_parser import parse_available_checks


@pytest.mark.parametrize(("shown", "expected"), [("248", 248), ("0", 0), ("1&nbsp;248", 1248)])
def test_reads_account_counter_not_package_cells(shown, expected):
    body = f"""<main><div><b>5</b>check done</div>
      <div><b>{shown}</b><span>check available</span></div>
      <table><tr><td>999</td><td>check available</td></tr></table></main>"""
    assert parse_available_checks(body) == expected


@pytest.mark.parametrize(
    "body", ["<form>Login</form>", "<p>1 check available</p><p>2 check available</p>"]
)
def test_missing_or_ambiguous_counter_fails(body):
    with pytest.raises(ValueError):
        parse_available_checks(body)


def test_client_reads_only_package_page(monkeypatch):
    client = GetBlockAMLClient(identity="test", password="test")
    response = MagicMock(status_code=200, headers={"Content-Type": "text/html; charset=UTF-8"})
    response.__enter__.return_value = response
    response.iter_content.return_value = [b"<div><b>248</b><span>check available</span></div>"]
    get = MagicMock(return_value=response)
    monkeypatch.setattr(client, "_get", get)
    try:
        assert client.get_available_checks() == 248
        get.assert_called_once_with("/en/packages", allow_redirects=False, stream=True)
        response.__exit__.assert_called_once()
    finally:
        client.close()


def test_service_logs_in_and_closes_session_on_invalid_page(monkeypatch):
    service = AMLService(settings=MagicMock())
    client = MagicMock()
    client.get_available_checks.side_effect = ValueError
    monkeypatch.setattr(service, "_build_client", lambda: client)
    with pytest.raises(ValueError):
        service.get_available_checks()
    client.login.assert_called_once()
    client.close.assert_called_once()
    client.create_check.assert_not_called()


@pytest.mark.parametrize(
    ("chat_type", "manager", "expected"),
    [
        ("private", True, True),
        ("private", False, False),
        ("supergroup", True, False),
    ],
)
async def test_checks_only_discloses_balance_to_staff_in_allowed_chat(chat_type, manager, expected):
    lookup = MagicMock(return_value=248)
    handler = AMLHandler(
        SimpleNamespace(is_manager=AsyncMock(return_value=manager)),
        aml_queue_service=MagicMock(),
        billing=MagicMock(),
        get_available_checks=lookup,
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42, is_bot=False),
        sender_chat=None,
        chat=SimpleNamespace(id=42, type=chat_type),
        answer=AsyncMock(),
    )
    await handler._cmd_checks(message)
    assert lookup.call_count == int(expected)
    assert ("248" in message.answer.call_args.args[0]) is expected
