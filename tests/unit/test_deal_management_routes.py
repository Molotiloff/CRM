from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.models import UserRole
from services.messaging import DeferredMessenger
from tests.unit.test_api_deal_routes import FakeDealService, FakeSettlementService, _app


@pytest.mark.parametrize("role,expected", [(UserRole.manager, 200), (UserRole.cashier, 403)])
def test_transfer_adjustment_uses_shared_workflow_and_authenticated_actor(role, expected):
    service = FakeDealService()
    service.row.update({"source_kind": "client_transfer", "deal_type": "client_transfer", "client_chat_id": -123, "status": "done"})
    workflow = SimpleNamespace(adjust=AsyncMock())
    app = _app(service, role=role)
    app.state.container = SimpleNamespace(messenger=object(), crm=SimpleNamespace(client_transfer_workflow=workflow))
    response = TestClient(app).post("/api/v1/deals/1/transfer-adjustment", json={
        "newAmount": "150.25", "expectedAmount": "100", "idempotencyKey": "edit-1", "allowNegative": True,
    })
    assert response.status_code == expected, response.text
    if role is UserRole.manager:
        command = workflow.adjust.call_args.args[0]
        assert command.from_chat_id == -123
        assert command.new_amount == Decimal("150.25")
        assert command.expected_amount == Decimal("100")
        assert command.actor_tg_user_id == 42
        assert command.source_ref == "crm:1:edit-1"
        assert command.allow_negative
    else:
        workflow.adjust.assert_not_awaited()


def test_transfer_adjustment_rejects_non_transfer_and_unavailable_delivery():
    service = FakeDealService()
    app = _app(service)
    response = TestClient(app).post("/api/v1/deals/1/transfer-adjustment", json={"expectedAmount": "25", "idempotencyKey": "cancel"})
    assert response.status_code == 400
    service.row.update({"source_kind": "client_transfer", "deal_type": "client_transfer"})
    app.state.container = SimpleNamespace(messenger=DeferredMessenger())
    response = TestClient(app).post("/api/v1/deals/1/transfer-adjustment", json={"expectedAmount": "25", "idempotencyKey": "cancel"})
    assert response.status_code == 409


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity"])
def test_transfer_adjustment_rejects_invalid_amount(amount):
    response = TestClient(_app(FakeDealService())).post("/api/v1/deals/1/transfer-adjustment", json={
        "expectedAmount": "25", "newAmount": amount, "idempotencyKey": "edit",
    })
    assert response.status_code == 422


def test_cash_ready_reuses_cash_service_and_does_not_post_money():
    service = FakeDealService()
    service.row.update({"source_kind": "cash", "body": {"req_id": "Б-123"}})
    service.publish_committed_status_change = AsyncMock()

    async def mark_ready(**kwargs):
        service.row["status"] = "ready_for_cash_settlement"

    cash = SimpleNamespace(mark_ready=AsyncMock(side_effect=mark_ready))
    app = _app(service)
    app.state.container = SimpleNamespace(accounting=SimpleNamespace(cash_settlements=cash))
    response = TestClient(app).post("/api/v1/deals/1/cash-ready", json={})
    assert response.status_code == 200, response.text
    assert response.json()["deal"]["status"] == "ready_for_cash_settlement"
    cash.mark_ready.assert_awaited_once_with(request_id="Б-123", actor_tg_user_id=42)
    service.publish_committed_status_change.assert_awaited_once()


def test_cash_ready_rejects_completed_request():
    service = FakeDealService()
    service.row.update({"source_kind": "cash", "status": "done"})
    response = TestClient(_app(service)).post("/api/v1/deals/1/cash-ready")
    assert response.status_code == 409


def test_settlements_reads_through_application_service():
    settlement = FakeSettlementService()
    settlement.list_for_deal = AsyncMock(return_value=[])
    app = _app(FakeDealService(), settlement=settlement)
    assert TestClient(app).get("/api/v1/deals/1/settlements").json() == []
    settlement.list_for_deal.assert_awaited_once_with(1)
    assert TestClient(app).get("/api/v1/deals/404/settlements").status_code == 404
