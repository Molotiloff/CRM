from __future__ import annotations

import io
import json
import logging

from observability import (
    bind_log_context,
    current_log_context,
    logged_operation,
    operation_context,
)
from observability.logging import StructuredLogFormatter


async def test_nested_async_operation_inherits_context_and_resets_afterward() -> None:
    @logged_operation("deal.update")
    async def nested() -> tuple[str | None, str | None, str | None]:
        bind_log_context(deal_id=7)
        context = current_log_context()
        return context.operation_id, context.deal_id, context.request_id

    with operation_context("exchange.create", request_id=42):
        outer_operation_id = current_log_context().operation_id
        nested_context = await nested()

    assert nested_context == (outer_operation_id, "7", "42")
    assert current_log_context().operation_id is None
    assert current_log_context().deal_id is None
    assert current_log_context().request_id is None


def test_structured_formatter_emits_context_and_extra_fields() -> None:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(StructuredLogFormatter())
    logger = logging.getLogger("structured-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    with operation_context("deal.change_status", deal_id=7, request_id=42):
        logger.info(
            "Deal status changed",
            extra={"event": "deal.status_changed", "status": "fixed"},
        )

    payload = json.loads(output.getvalue())
    assert payload["level"] == "INFO"
    assert payload["message"] == "Deal status changed"
    assert payload["operation"] == "deal.change_status"
    assert payload["operation_id"].startswith("deal.change_status:")
    assert payload["deal_id"] == "7"
    assert payload["request_id"] == "42"
    assert payload["event"] == "deal.status_changed"
    assert payload["status"] == "fixed"
