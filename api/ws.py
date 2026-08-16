from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from api.auth import AuthError, decode_access_token
from api.dependencies import ROLE_LEVELS
from api.models import ApiUser, UserRole
from services.crm.deal_events import DealEventBus

router = APIRouter(tags=["deals"])


@router.websocket("/ws/deals")
async def deals_websocket(websocket: WebSocket) -> None:
    user = await _authenticate(websocket)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    event_bus: DealEventBus = websocket.app.state.deal_event_bus
    await websocket.accept()
    try:
        async with event_bus.subscribe() as queue:
            while True:
                event = await queue.get()
                await websocket.send_json(event.as_dict())
    except WebSocketDisconnect:
        return


async def _authenticate(websocket: WebSocket) -> ApiUser | None:
    config = websocket.app.state.config
    repository = websocket.app.state.user_repository
    token = websocket.cookies.get("crm_access_token") or websocket.query_params.get("token")
    if not token:
        if config.api_dev_auth_bypass:
            user = await repository.get_dev_user(config.api_dev_tg_user_id)
            return _allowed(user)
        return None
    try:
        payload = decode_access_token(token, bot_token=config.bot_token)
        user = await repository.get_active_by_tg_user_id(int(payload.sub))
    except (AuthError, ValueError):
        return None
    return _allowed(user)


def _allowed(user: ApiUser | None) -> ApiUser | None:
    if user is None or ROLE_LEVELS[user.role] < ROLE_LEVELS[UserRole.cashier]:
        return None
    return user
