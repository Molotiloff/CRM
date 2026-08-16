from __future__ import annotations

import contextlib
import contextvars
import functools
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class LogContext:
    operation: str | None = None
    operation_id: str | None = None
    deal_id: str | None = None
    request_id: str | None = None


_log_context: contextvars.ContextVar[LogContext | None] = contextvars.ContextVar(
    "structured_log_context",
    default=None,
)


def current_log_context() -> LogContext:
    return _log_context.get() or LogContext()


def bind_log_context(
    *,
    deal_id: object | None = None,
    request_id: object | None = None,
) -> None:
    current = current_log_context()
    _log_context.set(
        replace(
            current,
            deal_id=_identifier(deal_id) if deal_id is not None else current.deal_id,
            request_id=(_identifier(request_id) if request_id is not None else current.request_id),
        )
    )


@contextlib.contextmanager
def operation_context(
    operation: str,
    *,
    deal_id: object | None = None,
    request_id: object | None = None,
) -> Iterator[LogContext]:
    current = current_log_context()
    context = replace(
        current,
        operation=current.operation or operation,
        operation_id=current.operation_id or f"{operation}:{uuid.uuid4().hex}",
        deal_id=_identifier(deal_id) if deal_id is not None else current.deal_id,
        request_id=(_identifier(request_id) if request_id is not None else current.request_id),
    )
    token = _log_context.set(context)
    try:
        yield context
    finally:
        _log_context.reset(token)


def logged_operation(
    operation: str,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    def decorator(
        function: Callable[P, Awaitable[T]],
    ) -> Callable[P, Awaitable[T]]:
        @functools.wraps(function)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            with operation_context(operation):
                return await function(*args, **kwargs)

        return wrapper

    return decorator


class StructuredLogFormatter(logging.Formatter):
    _STANDARD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        context = current_log_context()
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({key: value for key, value in asdict(context).items() if value is not None})
        payload.update(self._extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)

    def _extra_fields(self, record: logging.LogRecord) -> Mapping[str, Any]:
        return {
            key: value
            for key, value in record.__dict__.items()
            if key not in self._STANDARD_FIELDS
            and key not in {"message", "asctime"}
            and not key.startswith("_")
        }


def configure_logging(*, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredLogFormatter())
    logging.basicConfig(
        level=level,
        handlers=[handler],
        force=True,
    )


def _identifier(value: object) -> str:
    return str(value).strip()
