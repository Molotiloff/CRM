from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import AuthError
from api.exception_handlers import register_exception_handlers
from api.queries import ClientNotFoundError
from domain import DomainStateError, DomainValidationError
from services.crm.deal_service import DealNotFoundError


@pytest.mark.parametrize(
    ("exception", "expected_status"),
    (
        (AuthError("invalid login"), 401),
        (ClientNotFoundError(42), 404),
        (DealNotFoundError("deal not found"), 404),
        (DomainValidationError("invalid value"), 400),
        (DomainStateError("invalid state"), 409),
    ),
)
def test_mapped_exception_returns_stable_http_error(
    exception: Exception,
    expected_status: int,
) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/failure")
    async def failure() -> None:
        raise exception

    response = TestClient(app).get("/failure")

    assert response.status_code == expected_status
    assert response.json() == {"detail": str(exception)}
