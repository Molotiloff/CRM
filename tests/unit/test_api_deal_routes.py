from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import get_current_user
from api.exception_handlers import register_exception_handlers
from api.models import ApiUser, UserRole
from api.routers import deals
from api.schemas.common import ErrorResponse
from api.schemas.deals import DealDetailsResponse, DealsPageResponse
from domain import Deal, DomainStateError, SettlementReviewStatus
from domain.accounting_flows import SettlementResolution
from services.crm.deal_service import DealNotFoundError
from services.payment_watch.settlement_models import SettlementResult


def test_deal_routes_expose_list_create_update_and_status() -> None:
    service = FakeDealService()
    client = TestClient(_app(service))

    listed = client.get("/api/v1/deals?status=new&type=sale")
    created = client.post(
        "/api/v1/deals",
        json={"dealType": "sale", "city": "Екб", "body": {"currency": "USDT"}},
    )
    updated = client.patch("/api/v1/deals/1", json={"comment": "updated"})
    changed = client.post(
        "/api/v1/deals/1/status",
        json={"status": "fixed", "comment": "rate fixed"},
    )

    assert listed.status_code == 200
    assert listed.json()["deals"][0]["asset"] == "USDT"
    DealsPageResponse.model_validate(listed.json())
    assert created.status_code == 201
    DealDetailsResponse.model_validate(created.json())
    assert service.created.actor_user_id == 1
    DealDetailsResponse.model_validate(updated.json())
    assert updated.json()["comment"] == "updated"
    DealDetailsResponse.model_validate(changed.json())
    assert changed.json()["deal"]["status"] == "fixed"
    assert service.status_payload == {"comment": "rate fixed"}


def test_deal_detail_route_returns_success_and_not_found_contracts() -> None:
    client = TestClient(_app(FakeDealService()))

    found = client.get("/api/v1/deals/1")
    missing = client.get("/api/v1/deals/404")

    assert found.status_code == 200
    DealDetailsResponse.model_validate(found.json())
    assert missing.status_code == 404
    error = ErrorResponse.model_validate(missing.json())
    assert error.detail == "Deal 404 was not found"


def test_deal_write_route_rejects_cashier() -> None:
    client = TestClient(_app(FakeDealService(), role=UserRole.cashier))

    response = client.post(
        "/api/v1/deals", json={"dealType": "sale", "city": "Екб"}
    )

    assert response.status_code == 403
    ErrorResponse.model_validate(response.json())


def test_deal_source_routes_delegate_to_mutation_service() -> None:
    mutation = FakeSourceMutationService()
    client = TestClient(_app(FakeDealService(), source_mutation=mutation))

    edited = client.patch(
        "/api/v1/deals/1/source",
        json={
            "exchange": {
                "operationId": 77,
                "recvCode": "RUB",
                "recvAmount": 9000,
                "payCode": "USDT",
                "payAmount": 100,
                "rate": 90,
            }
        },
    )
    canceled = client.post(
        "/api/v1/deals/1/cancel",
        json={"comment": "client request"},
    )

    assert edited.status_code == 200
    DealDetailsResponse.model_validate(edited.json())
    assert mutation.exchange_edit.operation_id == 77
    assert str(mutation.exchange_edit.recv_code) == "RUB"
    assert str(mutation.exchange_edit.pay_code) == "USDT"
    assert mutation.actor_name == "Manager"
    assert canceled.status_code == 200
    DealDetailsResponse.model_validate(canceled.json())
    assert mutation.cancel_call == (1, 1, "client request")


def test_deal_source_route_rejects_ambiguous_payload() -> None:
    client = TestClient(_app(FakeDealService()))

    response = client.patch("/api/v1/deals/1/source", json={})

    assert response.status_code == 422


def test_deal_source_route_maps_domain_validation_to_bad_request() -> None:
    client = TestClient(_app(FakeDealService()))

    response = client.patch(
        "/api/v1/deals/1/source",
        json={
            "exchange": {
                "operationId": 77,
                "recvCode": "RU B",
                "recvAmount": 9000,
                "payCode": "USDT",
                "payAmount": 100,
                "rate": 90,
            }
        },
    )

    assert response.status_code == 400
    error = ErrorResponse.model_validate(response.json())
    assert error.detail == "Invalid currency code: 'RU B'"


def test_deal_status_route_returns_conflict_contract() -> None:
    client = TestClient(_app(FakeDealService()))

    response = client.post(
        "/api/v1/deals/1/status",
        json={"status": "done", "payload": {"forceConflict": True}},
    )

    assert response.status_code == 409
    error = ErrorResponse.model_validate(response.json())
    assert error.detail == "Deal status changed concurrently"


def test_settlement_review_route_delegates_typed_resolution() -> None:
    settlement = FakeSettlementService()
    client = TestClient(_app(FakeDealService(), settlement=settlement))

    response = client.post(
        "/api/v1/settlements/7/resolve",
        json={"resolution": "accept_actual", "comment": "confirmed"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "settlementId": 7,
        "dealId": 1,
        "status": "resolved",
        "resolution": "accept_actual",
        "expected": "100",
        "actual": "90",
        "delta": "-10",
    }
    assert settlement.command.resolution is SettlementResolution.ACCEPT_ACTUAL
    assert settlement.command.actor_user_id == 1


def test_cancel_and_recreate_requires_replacement_terms() -> None:
    client = TestClient(_app(FakeDealService()))

    response = client.post(
        "/api/v1/settlements/7/resolve",
        json={"resolution": "cancel_and_recreate"},
    )

    assert response.status_code == 422


class FakeDealService:
    def __init__(self) -> None:
        self.row = _deal_row()
        self.created = None
        self.status_payload = None

    async def list_deals(self, filters):
        return [Deal.from_record(self.row)]

    async def get_deal(self, deal_id: int):
        if deal_id == 404:
            raise DealNotFoundError(f"Deal {deal_id} was not found")
        return Deal.from_record(self.row)

    async def create_deal(self, command):
        self.created = command
        return Deal.from_record(self.row)

    async def update_deal(self, deal_id: int, command):
        self.row.update(command.to_changes())
        return Deal.from_record(self.row)

    async def change_status(self, deal_id: int, command):
        if command.payload.to_dict().get("forceConflict"):
            raise DomainStateError("Deal status changed concurrently")
        self.row["status"] = command.status
        self.status_payload = command.payload.to_dict()
        return Deal.from_record(self.row)


class FakeSourceMutationService:
    def __init__(self) -> None:
        self.row = _deal_row()
        self.exchange_edit = None
        self.actor_name = None
        self.cancel_call = None

    async def edit_exchange(self, deal_id, command, *, actor_name):
        self.exchange_edit = command
        self.actor_name = actor_name
        return Deal.from_record(self.row)

    async def edit_cash(self, deal_id, command, *, actor_name):
        return Deal.from_record(self.row)

    async def cancel(self, deal_id, *, actor_user_id, comment):
        self.cancel_call = (deal_id, actor_user_id, comment)
        self.row["status"] = "canceled"
        return Deal.from_record(self.row)


class FakeSettlementService:
    def __init__(self) -> None:
        self.command = None

    async def resolve_review(self, command):
        self.command = command
        return SettlementResult(
            settlement_id=command.settlement_id,
            event_id=2,
            deal_id=1,
            expected=Decimal("100"),
            actual=Decimal("90"),
            delta=Decimal("-10"),
            status=SettlementReviewStatus.RESOLVED,
            created=False,
            resolution=command.resolution,
        )

def _app(
    service: FakeDealService,
    *,
    role: UserRole = UserRole.manager,
    source_mutation: FakeSourceMutationService | None = None,
    settlement: FakeSettlementService | None = None,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.state.deal_service = service
    app.state.deal_source_mutation_service = source_mutation or FakeSourceMutationService()
    app.state.deal_settlement_service = settlement or FakeSettlementService()
    app.dependency_overrides[get_current_user] = lambda: ApiUser(
        id=1,
        tg_user_id=42,
        display_name="Manager",
        role=role,
        cities=[],
        is_active=True,
    )
    app.include_router(deals.router)
    return app


def _deal_row() -> dict:
    now = datetime.now(UTC)
    return {
        "id": 1,
        "deal_no": 100001,
        "deal_type": "sale",
        "city": "Екб",
        "client_name": "Client Name",
        "status": "new",
        "created_by_name": "Manager",
        "source": "crm",
        "comment": None,
        "body": {"currency": "USDT", "rub_amount": 1000},
        "profit": 10,
        "created_at": now,
        "updated_at": now,
        "legs": [],
        "status_events": [],
    }
