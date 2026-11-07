from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.models import UserRole
from domain import Deal
from services.crm.invoice_service import invoice_amount
from tests.unit.test_api_deal_routes import FakeDealService, _app


def _client(role=UserRole.manager):
    fake = FakeDealService()
    fake.row.update({"deal_type": "invoice", "source_kind": "invoice", "body": {"received_usdt": "6000", "paid_usdt": "5900", "profit_usdt": "100", "currency": "USDT"}})
    service = SimpleNamespace(create=AsyncMock(return_value=Deal.from_record(fake.row)), post=AsyncMock(return_value=Deal.from_record(fake.row)), cancel=AsyncMock(return_value=Deal.from_record(fake.row)))
    app = _app(fake, role=role)
    app.state.container = SimpleNamespace(crm=SimpleNamespace(invoices=service))
    return TestClient(app), service


def test_invoice_routes_use_authenticated_actor_and_do_not_accept_client_profit():
    client, service = _client()
    response = client.post("/api/v1/deals/invoices", json={"clientId": 9, "city": "Екб", "receivedUsdt": "6000", "paidUsdt": "5900", "idempotencyKey": "invoice-1", "profitRub": 99999})
    assert response.status_code == 201, response.text
    assert response.json()["deal"]["exchangeAmount"] == "6000 → 5900 USDT"
    command = service.create.call_args.args[0]
    assert command.actor_user_id == 1 and command.received-command.paid == 100
    assert client.post("/api/v1/deals/1/invoice/post", json={"expectedReceivedUsdt": "6000", "expectedPaidUsdt": "6100", "allowLoss": True}).status_code == 200
    assert service.post.call_args.kwargs["actor_user_id"] == 1
    assert service.post.call_args.kwargs["allow_loss"] is True
    assert client.post("/api/v1/deals/1/invoice/cancel", json={"comment": "Не оплачен"}).status_code == 200


@pytest.mark.parametrize("path,payload", [("/deals/invoices", {"clientId": 9, "city": "екб", "receivedUsdt": "6000", "paidUsdt": "5900", "idempotencyKey": "invoice-1"}), ("/deals/1/invoice/post", {"expectedReceivedUsdt": "6000", "expectedPaidUsdt": "5900"}), ("/deals/1/invoice/cancel", {})])
def test_cashier_cannot_post_or_create_invoices(path, payload):
    client, service = _client(UserRole.cashier)
    assert client.post(f"/api/v1{path}", json=payload).status_code == 403
    service.post.assert_not_awaited()
    service.create.assert_not_awaited()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1", "0.000000001", "1000000000000000000"])
def test_invoice_amounts_reject_invalid_or_overprecise_input(value):
    client, _ = _client()
    assert client.post("/api/v1/deals/invoices", json={"clientId": 9, "city": "екб", "receivedUsdt": value, "paidUsdt": "1", "idempotencyKey": "test"}).status_code == 422
    with pytest.raises(ValueError):
        invoice_amount(Decimal(value))
