from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.auth import create_access_token
from api.models import ApiUser, UserRole
from api.ws import router
from domain import DealEventPayload
from services.crm.deal_events import DealEvent, DealEventBus

ALLOWED_ORIGIN = "https://skyex.work.gd"
BOT_TOKEN = "test-token"
JWT_SECRET = "test-jwt-secret-with-at-least-32-characters"


def test_deals_websocket_forwards_event_bus_messages() -> None:
    app, event_bus = _app(dev_auth_bypass=True)

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
    with client.websocket_connect(
        "/ws/deals",
        headers={"origin": ALLOWED_ORIGIN},
    ) as websocket:
        assert client.post("/test/publish").status_code == 200
        assert websocket.receive_json() == {
            "type": "deal.created",
            "deal_id": 7,
            "payload": {"status": "new"},
        }


def test_deals_websocket_accepts_authenticated_cookie() -> None:
    app, _ = _app(dev_auth_bypass=False)
    client = TestClient(app)
    client.cookies.set(
        "crm_access_token",
        create_access_token(
            tg_user_id=42,
            secret=JWT_SECRET,
            ttl_seconds=60,
        ),
    )

    with client.websocket_connect(
        "/ws/deals",
        headers={"origin": ALLOWED_ORIGIN},
    ):
        pass


@pytest.mark.parametrize("origin", [None, "https://attacker.example"])
def test_deals_websocket_rejects_untrusted_origin(origin: str | None) -> None:
    app, _ = _app(dev_auth_bypass=True)
    headers = {"origin": origin} if origin else {}

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with TestClient(app).websocket_connect("/ws/deals", headers=headers):
            pass

    assert exc_info.value.code == 1008


def test_deals_websocket_ignores_query_token() -> None:
    app, _ = _app(dev_auth_bypass=False)
    token = create_access_token(
        tg_user_id=42,
        secret=JWT_SECRET,
        ttl_seconds=60,
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with TestClient(app).websocket_connect(
            f"/ws/deals?token={token}",
            headers={"origin": ALLOWED_ORIGIN},
        ):
            pass

    assert exc_info.value.code == 1008


def test_deals_websocket_limits_concurrent_connections() -> None:
    app, _ = _app(dev_auth_bypass=True, max_subscribers=1)
    client = TestClient(app)
    headers = {"origin": ALLOWED_ORIGIN}

    with client.websocket_connect("/ws/deals", headers=headers):
        with client.websocket_connect("/ws/deals", headers=headers) as websocket:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_json()

    assert exc_info.value.code == 1013


def _app(
    *,
    dev_auth_bypass: bool,
    max_subscribers: int = 100,
) -> tuple[FastAPI, DealEventBus]:
    app = FastAPI()
    event_bus = DealEventBus(max_subscribers=max_subscribers)
    app.state.config = SimpleNamespace(
        api_dev_auth_bypass=dev_auth_bypass,
        api_dev_tg_user_id=None,
        api_ws_allowed_origins=[ALLOWED_ORIGIN],
        bot_token=BOT_TOKEN,
        crm_jwt_secret=JWT_SECRET,
    )
    app.state.user_repository = FakeUserRepository()
    app.state.deal_event_bus = event_bus
    app.include_router(router)
    return app, event_bus


class FakeUserRepository:
    async def get_dev_user(self, tg_user_id: int | None) -> ApiUser:
        return _user()

    async def get_active_by_tg_user_id(self, tg_user_id: int) -> ApiUser | None:
        return _user() if tg_user_id == 42 else None


def _user() -> ApiUser:
    return ApiUser(
        id=1,
        tg_user_id=42,
        display_name="Local Admin",
        role=UserRole.admin,
        cities=[],
        is_active=True,
    )
