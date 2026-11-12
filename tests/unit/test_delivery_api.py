from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.models import UserRole
from tests.unit.test_api_deal_routes import FakeDealService, _app


def delivery_app(role=UserRole.manager):
    fake = FakeDealService()
    fake.row.update({"deal_type": "delivery", "source_kind": "delivery"})
    app = _app(fake, role=role)
    service = SimpleNamespace(
        create=AsyncMock(),
        transition=AsyncMock(),
        couriers=AsyncMock(return_value=[]),
    )
    app.state.container = SimpleNamespace(crm=SimpleNamespace(deliveries=service))
    return app, service, fake


PAYLOAD = {
    "clientId": 1,
    "city": "Екб",
    "amount": "1000.00",
    "courierTgUserId": 44,
    "courierClientId": 2,
    "rewardRub": "123.45",
    "idempotencyKey": "test-delivery",
}


async def test_delivery_api_preserves_money_and_explicit_courier_wallet():
    app, service, fake = delivery_app()
    service.create.return_value = await fake.get_deal(1)
    response = TestClient(app).post("/api/v1/deals/deliveries", json=PAYLOAD)
    assert response.status_code == 201, response.text
    command = service.create.call_args.args[0]
    assert command.amount == Decimal("1000.00")
    assert command.reward == Decimal("123.45")
    assert command.courier_client_id == 2
    assert command.courier_tg_user_id == 44


@pytest.mark.parametrize(
    "field,value",
    [("rewardRub", "-1"), ("rewardRub", "1.001"), ("amount", "0"), ("courierTgUserId", 0)],
)
def test_delivery_api_rejects_invalid_values(field, value):
    app, service, _ = delivery_app()
    response = TestClient(app).post("/api/v1/deals/deliveries", json={**PAYLOAD, field: value})
    assert response.status_code == 422
    service.create.assert_not_awaited()


def test_cashier_cannot_create_or_complete_delivery():
    app, service, _ = delivery_app(UserRole.cashier)
    client = TestClient(app)
    assert client.post("/api/v1/deals/deliveries", json=PAYLOAD).status_code == 403
    assert (
        client.post(
            "/api/v1/deals/1/delivery/status",
            json={
                "status": "done",
                "expectedAmount": "1000",
                "expectedRewardRub": "123.45",
                "expectedCourierClientId": 2,
            },
        ).status_code
        == 403
    )
    service.create.assert_not_awaited()
    service.transition.assert_not_awaited()
