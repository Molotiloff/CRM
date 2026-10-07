from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import get_current_user
from api.exception_handlers import register_exception_handlers
from api.models import ApiUser, UserRole
from api.presentation.deals import build_deal_details, build_deals_page
from api.routers import deals
from api.schemas.common import ErrorResponse
from api.schemas.deals import DealDetailsResponse, DealsPageResponse, ExchangeDealCreateRequest
from domain import Deal, DomainStateError, SettlementReviewStatus
from domain.accounting_flows import SettlementResolution
from services.crm.deal_service import DealNotFoundError
from services.payment_watch.settlement_models import SettlementResult
from tests.fakes import FakeMessenger


@pytest.mark.parametrize("deal_type", ["sale", "purchase"])
def test_exchange_schema_preserves_decimal_referrer_spread(deal_type) -> None:
    payload = ExchangeDealCreateRequest.model_validate({
        "dealType": deal_type, "clientId": 1, "city": "Екб", "recvCode": "RUB",
        "recvAmount": "150000", "payCode": "USDT", "payAmount": "1704.545",
        "idempotencyKey": "spread", "referrerClientId": 2, "referrerSpreadRub": "0.10",
    })
    assert payload.referrerSpreadRub == Decimal("0.10")
    assert payload.referrerPercent == 0


def test_referrer_spread_route_requires_manager_and_uses_authenticated_actor() -> None:
    service = FakeDealService()
    service.pay_referrer_spread = AsyncMock(return_value=Deal.from_record(service.row))
    response = TestClient(_app(service)).post("/api/v1/deals/1/referrer-spread", json={"expectedAmount": "170.45", "referrerClientId": 2})
    assert response.status_code == 200
    service.pay_referrer_spread.assert_awaited_once_with(1, actor_user_id=1, actor_tg_user_id=42, expected_amount=Decimal("170.45"), expected_referrer_id=2)
    denied = TestClient(_app(service, role=UserRole.cashier)).post("/api/v1/deals/1/referrer-spread", json={"expectedAmount": "170.45", "referrerClientId": 2})
    assert denied.status_code == 403
    assert service.pay_referrer_spread.await_count == 1


def test_exchange_schema_rejects_negative_referrer_spread() -> None:
    with pytest.raises(ValueError):
        ExchangeDealCreateRequest.model_validate({
            "dealType": "sale", "clientId": 1, "city": "Екб", "recvCode": "RUB",
            "recvAmount": "150000", "payCode": "USDT", "payAmount": "1704.545",
            "idempotencyKey": "spread", "referrerSpreadRub": "-0.1",
        })


@pytest.mark.parametrize(("deal_type", "recv_amount", "pay_amount"), [
    ("sale", "150000", "1704.545"), ("purchase", "1704.545", "150000"),
])
def test_referrer_reward_is_displayed_in_rubles_with_kopeks(deal_type, recv_amount, pay_amount) -> None:
    service = FakeDealService()
    service.row.update({
        "deal_type": deal_type,
        "body": {"recv_amount": recv_amount, "pay_amount": pay_amount,
                 "referrer_client_id": 2, "referrer_spread_rub": "0.1"},
    })
    assert build_deal_details(Deal.from_record(service.row)).referrerRewardRub == 170.45
    # Old percentage metadata must not silently become a ruble-per-unit spread.
    service.row["body"] = {"referrer_client_id": 2, "referrer_percent": "0.1", "pay_amount": "1704.545"}
    assert build_deal_details(Deal.from_record(service.row)).referrerRewardRub is None


@pytest.mark.parametrize(("received", "paid"), [("USD", "USDT"), ("USDT", "THB"), ("EUR500", "USDW")])
def test_conversion_creation_reuses_telegram_exchange_workflow(received, paid) -> None:
    service = FakeDealService()
    service.row.update({
        "deal_type": "conversion", "source_kind": "exchange", "source_ref": "conversion-test",
        "exchange_client_req_id": "123", "profit": 0,
        "body": {"recv_code": received, "pay_code": paid, "recv_amount": "10", "pay_amount": "20"},
    })
    service.get_source_exchange = AsyncMock(side_effect=[None, Deal.from_record(service.row)])
    create = AsyncMock(return_value=SimpleNamespace(ok=True, request_chat_posted=True))
    con = AsyncMock()
    con.fetchrow.return_value = {"chat_id": -123, "name": "Client"}
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=con)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    app = _app(service)
    app.state.container = SimpleNamespace(
        messenger=FakeMessenger(), config=SimpleNamespace(request_chat_id=-456), pool=pool,
        exchange=SimpleNamespace(accept_short=SimpleNamespace(create_request=create)),
    )
    response = TestClient(app).post("/api/v1/deals/exchanges", json={
        "dealType": "conversion", "clientId": 1, "city": "Екб",
        "recvCode": received, "recvAmount": "10", "payCode": paid, "payAmount": "20",
        "idempotencyKey": "conversion-test",
    })
    assert response.status_code == 201, response.text
    create.assert_awaited_once()
    params = create.await_args.args[0]
    assert (params.recv_code, params.pay_code) == (received, paid)
    assert (params.recv_amount_expr, params.pay_amount_expr) == ("10", "20")
    assert params.source == "crm"
    assert response.json()["requestChatPosted"] is True
    assert response.json()["deal"]["direction"] == f"{received} → {paid}"
    assert response.json()["deal"]["asset"] == received
    assert response.json()["deal"]["exchangeAmount"] == f"10 {received} → 20 {paid}"
    assert response.json()["deal"]["amountRub"] == 0


@pytest.mark.parametrize(("received", "paid"), [("USD", "USD"), ("RUB", "USD"), ("USD", "RUB"), ("BTC", "USDT")])
def test_conversion_rejects_invalid_direction_before_creating_request(received, paid) -> None:
    response = TestClient(_app(FakeDealService())).post("/api/v1/deals/exchanges", json={
        "dealType": "conversion", "clientId": 1, "city": "Екб",
        "recvCode": received, "recvAmount": "10", "payCode": paid, "payAmount": "20",
        "idempotencyKey": "invalid-conversion",
    })
    assert response.status_code == 400


def test_conversion_can_be_written_to_table_through_shared_service(monkeypatch) -> None:
    service = FakeDealService()
    service.row.update({
        "deal_type": "conversion", "source_kind": "exchange", "source_ref": "conversion-test",
        "exchange_client_req_id": "123",
        "body": {"recv_code": "USD", "pay_code": "USDT", "recv_amount": "10", "pay_amount": "20"},
    })
    repo = SimpleNamespace(get_exchange_request_link=AsyncMock(return_value={"table_req_id": "456"}))

    async def finish(**kwargs):
        assert kwargs["repo"] is repo
        assert kwargs["deal_service"] is service
        assert kwargs["table_req_id"] == "456"
        assert kwargs["actor_user_id"] == 1
        service.row["status"] = "done"

    write = AsyncMock(side_effect=finish)
    monkeypatch.setattr(deals.RequestTableDoneService, "write_exchange_request_once", write)
    app = _app(service)
    app.state.container = SimpleNamespace(
        operational_repositories=SimpleNamespace(exchange_requests=repo), sheets_gateway=AsyncMock(),
    )
    response = TestClient(app).post("/api/v1/deals/1/table", json={})
    assert response.status_code == 200, response.text
    assert response.json()["deal"]["status"] == "done"
    write.assert_awaited_once()


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


def test_deal_list_accepts_accounting_import_source_kind() -> None:
    service = FakeDealService()
    service.row.update(
        {
            "source": "import",
            "source_kind": "accounting_import",
            "source_ref": "snapshot:deal:1",
        }
    )
    client = TestClient(_app(service))

    response = client.get("/api/v1/deals")

    assert response.status_code == 200
    DealsPageResponse.model_validate(response.json())


def test_deal_detail_route_returns_success_and_not_found_contracts() -> None:
    client = TestClient(_app(FakeDealService()))

    found = client.get("/api/v1/deals/1")
    missing = client.get("/api/v1/deals/404")

    assert found.status_code == 200
    DealDetailsResponse.model_validate(found.json())
    assert missing.status_code == 404
    error = ErrorResponse.model_validate(missing.json())
    assert error.detail == "Deal 404 was not found"


def test_crm_transfer_route_uses_shared_receipt_workflow() -> None:
    app = _app(FakeDealService())
    transfer = AsyncMock(return_value=SimpleNamespace(deal_id=1))
    app.state.container = SimpleNamespace(
        messenger=FakeMessenger(),
        crm=SimpleNamespace(client_transfer_workflow=SimpleNamespace(transfer=transfer)),
    )

    response = TestClient(app).post(
        "/api/v1/deals/client-transfers",
        json={
            "fromClientId": 11,
            "toClientId": 12,
            "amount": "25",
            "currency": "RUB",
            "idempotencyKey": "crm-transfer-key",
            "allowNegative": True,
        },
    )

    assert response.status_code == 201
    transfer.assert_awaited_once()
    command = transfer.await_args.args[0]
    assert command.source == "crm"
    assert command.from_client_id == 11
    assert command.to_client_id == 12
    assert command.allow_negative is True


def test_best_change_details_are_explicit_and_include_correction_link() -> None:
    now = datetime.now(UTC)
    details = build_deal_details(
        Deal.from_record(
            {
                "id": 18,
                "deal_no": 100018,
                "deal_type": "best_change",
                "city": "члб",
                "status": "done",
                "source": "tg_bot",
                "source_kind": "best_change",
                "created_by_name": "Manager",
                "profit": "175",
                "deal_at": "2026-08-31",
                "created_at": now,
                "updated_at": now,
                "corrected_from_deal_id": 17,
                "correction_reason": "Исправлен курс",
                "correction_actor_name": "Manager",
                "body": {
                    "operation_kind": "sale",
                    "qty_usdt": "1000",
                    "market_rate_rub": "87",
                    "client_rate_rub": "87.5",
                    "unit_spread_rub": "0.5",
                    "gross_spread_rub": "500",
                    "profit_pool_rub": "350",
                    "partner_share_rub": "175",
                    "skyex_profit_rub": "175",
                    "platform_fee_usdt": "1.714286",
                },
            }
        )
    )

    assert details.deal.clientName == "BestChange"
    assert details.deal.asset == "USDT"
    assert details.deal.direction == "USDT → RUB"
    assert details.deal.amountRub == 87500
    assert details.dealAt == "2026-08-31"
    assert details.bestChange is not None
    assert details.bestChange.qtyUsdt == 1000
    assert details.bestChange.marketRateRub == 87
    assert details.bestChange.clientRateRub == 87.5
    assert details.bestChange.originalDealId == "17"
    assert details.bestChange.correctionReason == "Исправлен курс"


def test_completed_cash_deal_uses_settled_rub_amount_in_list_and_details() -> None:
    now = datetime.now(UTC)

    def cash_deal(status: str, deal_type: str = "deposit") -> Deal:
        return Deal.from_record(
            {
                "id": 19,
                "deal_no": 103409,
                "deal_type": deal_type,
                "city": "екб",
                "status": status,
                "source": "crm",
                "source_kind": "cash",
                "created_by_name": "Manager",
                "created_at": now,
                "updated_at": now,
                "body": {
                    "currency": "RUB",
                    "amount": "10000.00",
                    "settled_qty": "1000.00",
                },
            }
        )

    for deal_type in ("deposit", "withdrawal"):
        completed = cash_deal("done", deal_type)
        assert build_deal_details(completed).deal.amountRub == 1000
        assert build_deals_page([completed]).deals[0].amountRub == 1000
        assert build_deal_details(completed).body["amount"] == "10000.00"
    assert build_deal_details(cash_deal("new")).deal.amountRub == 10000


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
