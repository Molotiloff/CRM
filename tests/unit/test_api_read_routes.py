from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from api.dependencies import get_current_user
from api.exception_handlers import register_exception_handlers
from api.models import ApiUser, UserRole
from api.queries import BalanceQueryService, ClientQueryService, DashboardQueryService
from api.rate_providers import (
    DashboardCityFinancialSnapshot,
    DashboardCurrencySnapshot,
    DashboardSheetSnapshot,
)
from api.routers import balances, clients, dashboard
from api.schemas.balances import BalancesSnapshotResponse
from api.schemas.clients import ClientDto, ClientsPageResponse, ClientTransactionsResponse
from api.schemas.common import ErrorResponse
from api.schemas.dashboard import DashboardResponse


class FakeReadRepository:
    async def count_clients(self, *, search=None, group=None) -> int:
        return 1

    async def list_clients(self, *, search=None, group=None, limit=100, offset=0) -> list[dict]:
        return [_client_row()]

    async def get_client(self, client_id: int) -> dict | None:
        return _client_row() if client_id == 1 else None

    async def balances_for_clients(self, client_ids: list[int]) -> list[dict]:
        return [
            {"client_id": 1, "currency_code": "RUB", "balance": Decimal("1500"), "precision": 2},
            {"client_id": 1, "currency_code": "USDT", "balance": Decimal("-10.5"), "precision": 2},
        ]

    async def stats_for_clients(self, client_ids: list[int]) -> dict[int, dict]:
        return {
            1: {
                "client_id": 1,
                "deals_count": 2,
                "turnover_rub": Decimal("3000"),
                "purchase_volume_rub": Decimal("1000"),
                "sale_volume_rub": Decimal("2000"),
            }
        }

    async def recent_transactions_by_client(
        self, client_ids: list[int], *, limit_per_client: int = 3
    ) -> dict[int, list[dict]]:
        return {1: [_transaction_row()]}

    async def client_transactions(self, client_id: int, *, limit: int = 50) -> list[dict]:
        return [_transaction_row()]

    async def nonzero_balances(self, *, currency=None, sign=None) -> list[dict]:
        return [
            {
                "client_id": 1,
                "client_name": "Test Client",
                "chat_id": -100,
                "currency_code": "USDT",
                "balance": Decimal("10"),
                "precision": 2,
            }
        ]

    async def latest_rub_rates(self) -> dict[str, Decimal]:
        return {"RUB": Decimal("1"), "USDT": Decimal("80")}

    async def cash_desks_balances(self) -> list[dict]:
        return [
            {"city": "екб", "currency_code": "RUB", "balance": Decimal("100000")},
            {"city": "екб", "currency_code": "USDT", "balance": Decimal("50")},
        ]

    async def active_schedule_counts(self) -> list[dict]:
        return [{"city": "екб", "active_count": 3}]

    async def active_exchange_request_count(self) -> int:
        return 2

    async def today_expenses_total(self) -> Decimal:
        return Decimal("2500")


class FakeRateProvider:
    async def get_rates(self) -> dict[str, Decimal]:
        return {"RUB": Decimal("1"), "USDT": Decimal("81")}

    async def get_snapshot(self, *, today: date) -> DashboardSheetSnapshot:
        return _sheet_snapshot()


def test_clients_route_returns_frontend_page_dto() -> None:
    client = TestClient(_app())

    response = client.get("/api/v1/clients")

    assert response.status_code == 200
    data = response.json()
    assert data["totalClients"] == 1
    assert data["clients"][0]["id"] == "1"
    assert data["clients"][0]["balances"][1] == {"currency": "USDT", "amount": -10.5}
    assert data["clients"][0]["recentDeals"][0]["id"] == "#10"
    ClientsPageResponse.model_validate(data)


def test_client_detail_route_returns_client_contract() -> None:
    response = TestClient(_app()).get("/api/v1/clients/1")

    assert response.status_code == 200
    client = ClientDto.model_validate(response.json())
    assert client.id == "1"


def test_client_detail_route_returns_documented_not_found_contract() -> None:
    response = TestClient(_app()).get("/api/v1/clients/404")

    assert response.status_code == 404
    error = ErrorResponse.model_validate(response.json())
    assert error.detail == "Client 404 not found"


def test_balances_route_returns_snapshot_dto() -> None:
    client = TestClient(_app())

    response = client.get("/api/v1/balances")

    assert response.status_code == 200
    data = response.json()
    assert data["clients"][0]["balanceRub"] == 800
    assert data["summaries"][-1] == {
        "code": "ALL",
        "label": "Все балансы",
        "value": 800,
        "changePercent": None,
        "tone": "green",
    }
    BalancesSnapshotResponse.model_validate(data)


def test_client_transactions_route_returns_history() -> None:
    client = TestClient(_app())

    response = client.get("/api/v1/clients/1/transactions")

    assert response.status_code == 200
    data = response.json()
    assert data["items"][0]["currency"] == "RUB"
    assert data["items"][0]["balanceAfter"] == 5000
    ClientTransactionsResponse.model_validate(data)


def test_dashboard_route_returns_live_snapshot_dto() -> None:
    client = TestClient(_app())

    response = client.get("/api/v1/dashboard")

    assert response.status_code == 200
    data = response.json()
    assert data["topMetrics"][2]["value"] == 5
    assert data["topMetrics"][0]["value"] == "43 528 093 ₽"
    assert data["topMetrics"][3]["value"] == "26 828 ₽"
    assert data["dailyIndicators"][0]["value"] == "2 029 905 ₽"
    assert data["dailyIndicators"][2]["value"] == "1 801 307 ₽"
    assert data["currencies"][1]["code"] == "USDT"
    assert data["currencies"][1]["factAmount"] == 5579
    assert data["citySummaries"][0]["city"] == "ЕКБ"
    assert data["citySummaries"][0]["rows"][3]["value"] == "812 518 ₽"
    DashboardResponse.model_validate(data)


def test_dashboard_rates_route_returns_numeric_rates() -> None:
    client = TestClient(_app())

    response = client.get("/api/v1/dashboard/rates")

    assert response.status_code == 200
    rates = TypeAdapter(dict[str, float]).validate_python(response.json())
    assert rates == {"RUB": 1.0, "USDT": 81.0}


def test_dashboard_route_rejects_manager_statistics_access() -> None:
    client = TestClient(_app(role=UserRole.manager))

    response = client.get("/api/v1/dashboard")

    assert response.status_code == 403
    ErrorResponse.model_validate(response.json())


def _app(*, role: UserRole = UserRole.accountant) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    read_repository = FakeReadRepository()
    rate_provider = FakeRateProvider()
    app.state.client_queries = ClientQueryService(read_repository)
    app.state.balance_queries = BalanceQueryService(read_repository)
    app.state.dashboard_queries = DashboardQueryService(
        balance_repository=read_repository,
        dashboard_repository=read_repository,
        rate_provider=rate_provider,
        sheet_provider=rate_provider,
    )
    app.dependency_overrides[get_current_user] = lambda: ApiUser(
        id=1,
        tg_user_id=42,
        display_name="Manager",
        role=role,
        cities=[],
        is_active=True,
    )
    app.include_router(clients.router)
    app.include_router(balances.router)
    app.include_router(dashboard.router)
    return app


def _client_row() -> dict:
    return {
        "id": 1,
        "chat_id": -100,
        "name": "Test Client",
        "client_group": "VIP",
        "created_at": datetime(2026, 7, 31, 8, 0, tzinfo=UTC),
    }


def _transaction_row() -> dict:
    return {
        "id": 10,
        "txn_at": datetime(2026, 7, 31, 8, 30, tzinfo=UTC),
        "amount": Decimal("500"),
        "balance_after": Decimal("5000"),
        "comment": "test",
        "source": "unit",
        "currency_code": "RUB",
        "group_name": None,
        "actor_name": None,
    }


def _sheet_snapshot() -> DashboardSheetSnapshot:
    currency = DashboardCurrencySnapshot(
        amount=Decimal("2597"),
        rate=Decimal("85.28"),
        rub_value=Decimal("221467"),
        client_amount=Decimal("2409"),
        fact_amount=Decimal("5579"),
    )
    return DashboardSheetSnapshot(
        income=Decimal("2029905"),
        expense=Decimal("228598"),
        profit=Decimal("1801307"),
        turnover=Decimal("36833394"),
        gap=Decimal("-23"),
        today_expense=Decimal("26828"),
        fact_turnover=Decimal("43528093"),
        total_rub=Decimal("4404248"),
        total_balances=Decimal("-39123845"),
        fact_rub=Decimal("-34719596"),
        rub_cash=Decimal("2081592"),
        rub_in_currency=Decimal("2322656"),
        client_balances=Decimal("-38694123"),
        skyex_balances=Decimal("-429722"),
        currencies={"USDT": currency},
        cities={
            "екб": DashboardCityFinancialSnapshot(
                income=Decimal("839883"),
                expense=Decimal("27365"),
                profit=Decimal("812518"),
            )
        },
    )
