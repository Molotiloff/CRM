from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import get_current_user
from api.exception_handlers import register_exception_handlers
from api.models import ApiUser, UserRole
from api.routers import manual_cash
from services.accounting import (
    ManualCashAccount,
    ManualCashAccountCode,
    ManualCashMove,
)


class FakeManualCashService:
    def __init__(self) -> None:
        self.recorded = None
        self.reversed = None

    async def snapshot(self, *, limit: int = 20):
        assert limit == 20
        return (
            (
                ManualCashAccount(
                    code=ManualCashAccountCode.POETS,
                    name="Поэты",
                    balance=Decimal("-57565"),
                ),
            ),
            (),
        )

    async def record(self, command):
        self.recorded = command
        return _move()

    async def reverse(self, command):
        self.reversed = command
        return _move(operation="reversal", reversal_of_id=10)


def test_manual_cash_routes_map_commands_and_results() -> None:
    service = FakeManualCashService()
    client = TestClient(_app(service))

    snapshot = client.get("/api/v1/dashboard/manual-cash")
    recorded = client.post(
        "/api/v1/dashboard/manual-cash/moves",
        json={
            "accountCode": "moscow_poets",
            "operation": "inflow",
            "amount": "100.005",
            "effectiveAt": "2026-09-02",
            "comment": "Пополнение",
            "idempotencyKey": "manual:test:record",
        },
    )
    reversed_response = client.post(
        "/api/v1/dashboard/manual-cash/moves/10/reverse",
        json={
            "comment": "Исправление",
            "idempotencyKey": "manual:test:reverse",
        },
    )

    assert snapshot.status_code == 200
    assert snapshot.json()["accounts"][0]["balance"] == "-57565"
    assert recorded.status_code == 201
    assert service.recorded.actor_user_id == 7
    assert service.recorded.amount == Decimal("100.01")
    assert reversed_response.status_code == 200
    assert service.reversed.move_id == 10


def test_manual_cash_routes_require_accountant_role() -> None:
    response = TestClient(_app(FakeManualCashService(), UserRole.manager)).get(
        "/api/v1/dashboard/manual-cash"
    )

    assert response.status_code == 403


def _app(
    service: FakeManualCashService,
    role: UserRole = UserRole.accountant,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.state.manual_cash_service = service
    app.dependency_overrides[get_current_user] = lambda: ApiUser(
        id=7,
        tg_user_id=42,
        display_name="Бухгалтер",
        role=role,
        cities=[],
        is_active=True,
    )
    app.include_router(manual_cash.router)
    return app


def _move(
    *,
    operation: str = "inflow",
    reversal_of_id: int | None = None,
) -> ManualCashMove:
    return ManualCashMove(
        id=11,
        account_code=ManualCashAccountCode.POETS,
        operation=operation,
        amount=Decimal("100.01"),
        balance_after=Decimal("-57464.99"),
        effective_at=date(2026, 9, 2),
        comment="Операция",
        actor_name="Бухгалтер",
        reversal_of_id=reversal_of_id,
        reversed=False,
    )
