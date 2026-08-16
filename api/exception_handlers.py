from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from api.auth import AuthError
from api.queries import ClientNotFoundError
from domain import DomainStateError, DomainValidationError
from services.crm.deal_service import DealNotFoundError

EXCEPTION_STATUS_MAP: Mapping[type[Exception], int] = {
    AuthError: status.HTTP_401_UNAUTHORIZED,
    ClientNotFoundError: status.HTTP_404_NOT_FOUND,
    DealNotFoundError: status.HTTP_404_NOT_FOUND,
    DomainValidationError: status.HTTP_400_BAD_REQUEST,
    DomainStateError: status.HTTP_409_CONFLICT,
}


def register_exception_handlers(app: FastAPI) -> None:
    for exception_type in EXCEPTION_STATUS_MAP:
        app.add_exception_handler(exception_type, mapped_exception_handler)


async def mapped_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=_status_for(exc),
        content={"detail": str(exc)},
    )


def _status_for(exc: Exception) -> int:
    for exception_type in type(exc).__mro__:
        status_code = EXCEPTION_STATUS_MAP.get(exception_type)
        if status_code is not None:
            return status_code
    return status.HTTP_500_INTERNAL_SERVER_ERROR
