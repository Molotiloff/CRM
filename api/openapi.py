from __future__ import annotations

from http import HTTPStatus
from typing import Any

from api.schemas.common import ErrorResponse


def error_responses(*status_codes: int) -> dict[int, dict[str, Any]]:
    return {
        status_code: {
            "description": HTTPStatus(status_code).phrase,
            "model": ErrorResponse,
        }
        for status_code in status_codes
    }
