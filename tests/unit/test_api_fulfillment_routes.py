from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import get_current_user
from api.models import ApiUser, UserRole
from api.routers import fulfillments
from api.schemas.fulfillments import FulfillmentQueueResponse
from services.accounting.fulfillment_models import (
    FulfillmentQueueItem,
    FulfillmentQueueSummary,
    FulfillmentRequestKind,
    FulfillmentStatus,
)


def test_fulfillment_routes_list_reorder_and_cancel() -> None:
    service = FakeFulfillmentService()
    client = TestClient(_app(service))

    listed = client.get("/api/v1/fulfillments")
    reordered = client.post(
        "/api/v1/fulfillments/1/reorder",
        json={"beforeItemId": 2},
    )
    canceled = client.post(
        "/api/v1/fulfillments/1/cancel",
        json={"reason": "client canceled"},
    )

    assert listed.status_code == 200
    response = FulfillmentQueueResponse.model_validate(listed.json())
    assert response.queuedQty == "30"
    assert response.queueShortageQty == "5"
    assert [item.requestKind for item in response.items] == ["sale"]
    assert reordered.status_code == 200
    assert service.reorder_command.before_item_id == 2
    assert service.reorder_command.actor_user_id == 1
    assert canceled.status_code == 200
    assert service.cancel_call == (1, "client canceled")


def test_fulfillment_mutations_require_manager() -> None:
    client = TestClient(_app(FakeFulfillmentService(), role=UserRole.cashier))

    response = client.post(
        "/api/v1/fulfillments/1/reorder",
        json={"beforeItemId": None},
    )

    assert response.status_code == 403


class FakeFulfillmentService:
    def __init__(self) -> None:
        self.item = FulfillmentQueueItem(
            id=1,
            deal_id=11,
            request_kind=FulfillmentRequestKind.SALE,
            qty=Decimal("30"),
            sequence_no=1024,
            status=FulfillmentStatus.QUEUED,
            created_by=1,
            created_at=datetime.now(UTC),
            reordered_by=None,
            reordered_at=None,
        )
        self.reorder_command = None
        self.cancel_call = None

    async def list_active(self):
        return [self.item]

    async def summary(self):
        return FulfillmentQueueSummary(
            queued_qty=Decimal("30"),
            usdt_fact=Decimal("25"),
            queue_shortage_qty=Decimal("5"),
            onchain_liquid_qty=Decimal("0"),
        )

    async def reorder(self, command):
        self.reorder_command = command
        return self.item

    async def cancel(self, *, item_id: int, reason: str):
        self.cancel_call = (item_id, reason)
        return self.item


def _app(
    service: FakeFulfillmentService,
    *,
    role: UserRole = UserRole.manager,
) -> FastAPI:
    app = FastAPI()
    app.state.fulfillment_queue_service = service
    app.dependency_overrides[get_current_user] = lambda: ApiUser(
        id=1,
        tg_user_id=42,
        display_name="Manager",
        role=role,
        cities=[],
        is_active=True,
    )
    app.include_router(fulfillments.router)
    return app
