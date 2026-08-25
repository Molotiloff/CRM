from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute

from api.models import ApiUser, HealthResponse, LoginResponse, MetricsResponse
from api.routers import auth, balances, clients, dashboard, deals, health
from api.schemas.balances import BalancesSnapshotResponse
from api.schemas.clients import ClientDto, ClientsPageResponse, ClientTransactionsResponse
from api.schemas.dashboard import DashboardResponse
from api.schemas.deals import DealDetailsResponse, DealsPageResponse

RouteKey = tuple[str, str]

AUTH_ERRORS = frozenset({401})
ROLE_ERRORS = frozenset({401, 403})
DEAL_COMMAND_ERRORS = frozenset({400, 401, 403, 404, 409})
API_ROUTERS = (
    health.router,
    auth.router,
    clients.router,
    balances.router,
    dashboard.router,
    deals.router,
)

EXPECTED_SUCCESS_MODELS: dict[RouteKey, tuple[int, object]] = {
    ("GET", "/api/v1/health"): (200, HealthResponse),
    ("GET", "/api/v1/metrics"): (200, MetricsResponse),
    ("POST", "/api/v1/auth/telegram"): (200, LoginResponse),
    ("GET", "/api/v1/me"): (200, ApiUser),
    ("GET", "/api/v1/clients"): (200, ClientsPageResponse),
    ("GET", "/api/v1/clients/{client_id}"): (200, ClientDto),
    ("GET", "/api/v1/clients/{client_id}/transactions"): (
        200,
        ClientTransactionsResponse,
    ),
    ("GET", "/api/v1/balances"): (200, BalancesSnapshotResponse),
    ("GET", "/api/v1/dashboard"): (200, DashboardResponse),
    ("GET", "/api/v1/dashboard/rates"): (200, dict[str, float]),
    ("GET", "/api/v1/deals"): (200, DealsPageResponse),
    ("POST", "/api/v1/deals"): (201, DealDetailsResponse),
    ("GET", "/api/v1/deals/{deal_id}"): (200, DealDetailsResponse),
    ("PATCH", "/api/v1/deals/{deal_id}"): (200, DealDetailsResponse),
    ("POST", "/api/v1/deals/{deal_id}/status"): (200, DealDetailsResponse),
    ("PATCH", "/api/v1/deals/{deal_id}/source"): (200, DealDetailsResponse),
    ("POST", "/api/v1/deals/{deal_id}/cancel"): (200, DealDetailsResponse),
}

EXPECTED_ERROR_STATUSES: dict[RouteKey, frozenset[int]] = {
    ("GET", "/api/v1/health"): frozenset(),
    ("GET", "/api/v1/metrics"): frozenset(),
    ("POST", "/api/v1/auth/telegram"): frozenset({401, 403}),
    ("GET", "/api/v1/me"): AUTH_ERRORS,
    ("GET", "/api/v1/clients"): AUTH_ERRORS,
    ("GET", "/api/v1/clients/{client_id}"): frozenset({401, 404}),
    ("GET", "/api/v1/clients/{client_id}/transactions"): frozenset({401, 404}),
    ("GET", "/api/v1/balances"): AUTH_ERRORS,
    ("GET", "/api/v1/dashboard"): frozenset({401, 403, 503}),
    ("GET", "/api/v1/dashboard/rates"): ROLE_ERRORS,
    ("GET", "/api/v1/deals"): frozenset({400, 401, 403}),
    ("POST", "/api/v1/deals"): frozenset({400, 401, 403, 409}),
    ("GET", "/api/v1/deals/{deal_id}"): frozenset({401, 403, 404}),
    ("PATCH", "/api/v1/deals/{deal_id}"): DEAL_COMMAND_ERRORS,
    ("POST", "/api/v1/deals/{deal_id}/status"): DEAL_COMMAND_ERRORS,
    ("PATCH", "/api/v1/deals/{deal_id}/source"): DEAL_COMMAND_ERRORS,
    ("POST", "/api/v1/deals/{deal_id}/cancel"): DEAL_COMMAND_ERRORS,
}


def test_http_routes_declare_expected_success_response_models() -> None:
    routes = _api_routes()

    assert routes.keys() == EXPECTED_SUCCESS_MODELS.keys()
    for key, (expected_status, expected_model) in EXPECTED_SUCCESS_MODELS.items():
        route = routes[key]
        assert (route.status_code or 200) == expected_status
        assert route.response_model == expected_model


def test_openapi_documents_success_and_application_error_schemas() -> None:
    schema = _app().openapi()

    assert EXPECTED_ERROR_STATUSES.keys() == EXPECTED_SUCCESS_MODELS.keys()
    for key, (success_status, success_model) in EXPECTED_SUCCESS_MODELS.items():
        operation = schema["paths"][key[1]][key[0].lower()]
        success_schema = _response_schema(operation, success_status)
        if success_model == dict[str, float]:
            assert success_schema["type"] == "object"
            assert success_schema["additionalProperties"] == {"type": "number"}
        else:
            assert success_schema["$ref"] == (
                f"#/components/schemas/{success_model.__name__}"
            )

        documented_errors = {
            int(status_code)
            for status_code in operation["responses"]
            if status_code not in {str(success_status), "422"}
        }
        assert documented_errors == EXPECTED_ERROR_STATUSES[key]
        for status_code in documented_errors:
            assert _response_schema(operation, status_code)["$ref"] == (
                "#/components/schemas/ErrorResponse"
            )


def _app() -> FastAPI:
    app = FastAPI()
    for router in API_ROUTERS:
        app.include_router(router)
    return app


def _api_routes() -> dict[RouteKey, APIRoute]:
    return {
        (method, route.path): route
        for router in API_ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }


def _response_schema(operation: dict, status_code: int) -> dict:
    return operation["responses"][str(status_code)]["content"]["application/json"][
        "schema"
    ]
