"""Исполнение raw-SQL файлов миграций (migrations/sql/*) из ревизий Alembic.

SQL-файлы остаются самодостаточными (их можно прогнать и через psql), поэтому
могут содержать собственные BEGIN;/COMMIT; — при запуске под Alembic транзакцией
управляет он, и эти операторы вырезаются.

asyncpg не умеет multi-statement через prepared statements, поэтому скрипт
исполняется целиком на «сыром» asyncpg-соединении (simple query protocol).
"""
from __future__ import annotations

import re
from pathlib import Path

from alembic import context, op
from sqlalchemy.util import await_only

_SQL_DIR = Path(__file__).resolve().parent / "sql"
_TX_STATEMENT_RE = re.compile(r"^\s*(BEGIN|COMMIT)\s*;\s*$", re.IGNORECASE | re.MULTILINE)


def execute_sql_file(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8")
    sql = _TX_STATEMENT_RE.sub("", sql)
    if context.is_offline_mode():
        op.execute(sql)
        return
    asyncpg_connection = op.get_bind().connection.driver_connection
    await_only(asyncpg_connection.execute(sql))
