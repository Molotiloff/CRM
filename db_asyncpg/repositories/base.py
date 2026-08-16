from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import asyncpg


class ConnectionBoundRepo:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> None:
        self._pool = pool
        self._bound_connection = connection

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[asyncpg.Connection]:
        if self._bound_connection is not None:
            yield self._bound_connection
            return
        async with self._pool.acquire() as connection:
            yield connection


class BaseRepo(ConnectionBoundRepo):
    @staticmethod
    def _normalize_dt(v: datetime | date | str | None) -> datetime | None:
        """
        Принимает datetime/date/ISO-строку/None.
        Возвращает timezone-aware datetime (UTC) или None.
        """
        if v is None:
            return None
        if isinstance(v, datetime):
            dt = v
        elif isinstance(v, date):
            dt = datetime(v.year, v.month, v.day)
        elif isinstance(v, str):
            try:
                dt = datetime.fromisoformat(v)
            except ValueError:
                raise ValueError(f"Invalid datetime string: {v!r}") from None
        else:
            raise TypeError("Unsupported datetime type")

        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        return dt
