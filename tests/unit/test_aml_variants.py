from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from handlers.aml import parse_aml_request
from services.aml.aml_service import AMLService
from services.aml.getblock_client import GetBlockAMLClient
from services.aml.getblock_settings import GetBlockSettings
from services.aml.models import AMLCheckRequest


@pytest.mark.parametrize(
    ("command", "network", "kind"),
    [
        ("/амл TEsHvKtA74RJiHHXHdKVmjdEBaxy33CdK9", "trc20", "address"),
        ("/амл erc20 0x" + "a" * 40, "erc20", "address"),
        ("/амл ерц 0x" + "a" * 40, "erc20", "address"),
        ("/амл bep20 0x" + "a" * 40, "bep20", "address"),
        ("/амл беп 0x" + "a" * 40, "bep20", "address"),
        ("/амл btc bc1qg78tzyn0h04xqfkytx32qhldafllsv2v7wq3nx", "btc", "address"),
        ("/амл бтс bc1qg78tzyn0h04xqfkytx32qhldafllsv2v7wq3nx", "btc", "address"),
        ("/амл хэш " + "a" * 64, "trc20", "transaction"),
    ],
)
def test_parse_aml_variants(command: str, network: str, kind: str) -> None:
    request = parse_aml_request(command)
    assert request.network == network
    assert request.kind == kind


@pytest.mark.parametrize(
    "command",
    [
        "/амл",
        "/амл btc not-a-wallet",
        "/амл ерц not-a-wallet",
        "/амл bep20 TEsHvKtA74RJiHHXHdKVmjdEBaxy33CdK9",
        "/амл хэш 1234",
        "/амл eth 0x" + "a" * 40,
    ],
)
def test_parse_aml_rejects_invalid_input(command: str) -> None:
    with pytest.raises(ValueError):
        parse_aml_request(command)


@pytest.mark.parametrize(
    ("target", "currency", "token", "type_", "address", "tx_id", "direction"),
    [
        (AMLCheckRequest("T" + "a" * 33), "TRX", "9", "0", "T" + "a" * 33, "", "2"),
        (AMLCheckRequest("0x" + "a" * 40, network="erc20"), "ETH", "94252", "0", "0x" + "a" * 40, "", "2"),
        (AMLCheckRequest("0x" + "a" * 40, network="bep20"), "BSC", "9", "0", "0x" + "a" * 40, "", "2"),
        (AMLCheckRequest("bc1q" + "a" * 38, network="btc"), "BTC", "", "0", "bc1q" + "a" * 38, "", "2"),
        (
            AMLCheckRequest("a" * 64, kind="transaction"),
            "TRX", "9", "1", "T" + "b" * 33, "a" * 64, "1",
        ),
    ],
)
def test_aml_service_passes_network_and_tx_fields(
    monkeypatch: pytest.MonkeyPatch,
    target: AMLCheckRequest,
    currency: str,
    token: str,
    type_: str,
    address: str,
    tx_id: str,
    direction: str,
) -> None:
    settings = GetBlockSettings(
        identity="user", password="secret", lang="en", user_id="1",
        currency_code="TRX", token_id="9", aml_provider="2", direction="2",
        source="1", type_="0", reports_dir="reports",
    )
    client = Mock(spec=GetBlockAMLClient)
    client.BASE = "https://getblock.net"
    client.create_check.return_value = {"amlcheckup": "check-id"}
    client.get_transaction_check_form.return_value = "T" + "b" * 33
    client.get_report_preview_html.return_value = "ready"
    service = AMLService(settings=settings)
    monkeypatch.setattr(
        "services.aml.aml_service.parse_report_preview",
        lambda *_args, **_kwargs: {"type": "Transaction" if tx_id else "Address"},
    )
    monkeypatch.setattr("services.aml.aml_service.build_report_message", lambda _data: "report")

    result = service._check_wallet(client, target)

    assert result["message_text"] == "report"
    assert client.create_check.call_args.kwargs["currency_code"] == currency
    assert client.create_check.call_args.kwargs["token_id"] == token
    assert client.create_check.call_args.kwargs["type_"] == type_
    assert client.create_check.call_args.kwargs["checking_address"] == address
    assert client.create_check.call_args.kwargs["checking_tx_id"] == tx_id
    assert client.create_check.call_args.kwargs["direction"] == direction
    assert client.get_address_page.called is (not bool(tx_id))
    assert client.get_transaction_page.called is bool(tx_id)
    if tx_id:
        assert client.get_transaction_check_form.call_args.kwargs["currency_code"] == "USDTTRC20"


def test_getblock_form_puts_transaction_hash_and_recipient_in_distinct_fields() -> None:
    client = GetBlockAMLClient(identity="identity", password="password")
    page = SimpleNamespace(text='<input name="_csrf" value="hidden-token">')
    response = SimpleNamespace(
        status_code=302, url="https://getblock.net/en/explorer/order",
        headers={"x-redirect": "/en/report-preview/12345678-1234-1234-1234-123456789abc"},
        text="",
    )
    try:
        client.get_riskscore_page = Mock(return_value=page)  # type: ignore[method-assign]
        client._best_csrf_token = Mock(return_value="ajax-token")  # type: ignore[method-assign]
        client._post = Mock(return_value=response)  # type: ignore[method-assign]

        client.create_check(
            wallet="a" * 64, currency_code="TRX", token_id="9", user_id="1",
            aml_provider="2", direction="2", source="1", type_="1",
            checking_address="T" + "b" * 33, checking_tx_id="a" * 64,
        )

        data = client._post.call_args.kwargs["data"]
        assert data["CheckingForm[checking_hash]"] == "a" * 64
        assert data["CheckingForm[checking_address]"] == "T" + "b" * 33
        assert data["CheckingForm[checking_tx_id]"] == "a" * 64
        assert data["CheckingForm[type]"] == "1"
    finally:
        client.close()


def test_getblock_hash_preflight_resolves_hidden_address() -> None:
    client = GetBlockAMLClient(identity="identity", password="password")
    tx_hash = "a" * 64
    address = "T" + "b" * 33
    response = Mock(status_code=200)
    response.json.return_value = {
        "success": True,
        "message": (
            '<input type="hidden" name="CheckingForm[checking_address]" '
            f'value="{address}">'
        ),
    }
    try:
        client.refresh_ajax_csrf = Mock(return_value="csrf")  # type: ignore[method-assign]
        client._post = Mock(return_value=response)  # type: ignore[method-assign]

        assert client.get_transaction_check_form(
            tx_hash=tx_hash,
            currency_code="USDTTRC20",
            aml_provider="2",
            source="1",
        ) == address

        data = client._post.call_args.kwargs["data"]
        assert data == {
            "method": "getCheckForm",
            "checking_type": "1",
            "currency_code": "USDTTRC20",
            "checking_hash": tx_hash,
            "source": "1",
            "hot_search": "true",
            "aml_provider": "2",
        }
    finally:
        client.close()


def test_getblock_hash_preflight_rejects_missing_address_before_order() -> None:
    client = GetBlockAMLClient(identity="identity", password="password")
    response = Mock(status_code=200)
    response.json.return_value = {"success": True, "message": "<div>No transaction</div>"}
    try:
        client.refresh_ajax_csrf = Mock(return_value="csrf")  # type: ignore[method-assign]
        client._post = Mock(return_value=response)  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="не определил адрес"):
            client.get_transaction_check_form(
                tx_hash="a" * 64, currency_code="USDTTRC20",
                aml_provider="2", source="1",
            )
    finally:
        client.close()
