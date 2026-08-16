from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.models import ApiUser, UserRole
from api.ws import router
from domain import DealEventPayload
from services.crm.deal_events import DealEvent, DealEventBus


def test_deals_websocket_forwards_event_bus_messages() -> None:
    app = FastAPI()
    event_bus = DealEventBus()
    app.state.config = SimpleNamespace(
        api_dev_auth_bypass=True,
        api_dev_tg_user_id=None,
        bot_token="test-token",
    )
    app.state.user_repository = FakeUserRepository()
    app.state.deal_event_bus = event_bus
    app.include_router(router)

    @app.post("/test/publish")
    async def publish() -> None:
        await event_bus.publish(
            DealEvent(
                type="deal.created",
                deal_id=7,
                payload=DealEventPayload({"status": "new"}),
            )
        )

    client = TestClient(app)
    with client.websocket_connect("/ws/deals") as websocket:
        assert client.post("/test/publish").status_code == 200
        assert websocket.receive_json() == {
            "type": "deal.created",
            "deal_id": 7,
            "payload": {"status": "new"},
        }


class FakeUserRepository:
    async def get_dev_user(self, tg_user_id: int | None) -> ApiUser:
        return ApiUser(
            id=1,
            tg_user_id=42,
            display_name="Local Admin",
            role=UserRole.admin,
            cities=[],
            is_active=True,
        )
