"""Общая инфраструктура тестов (workflow_crm.md C0.2 / workflow.md 3.1).

tests/unit — чистые функции, БД не нужна.
tests/db   — денежное ядро против настоящего Postgres: одноразовая БД
             `crm_skyex_test` создаётся локальным createdb и доводится до head
             миграциями Alembic (заодно каждый прогон проверяет миграции).

Переопределение: TEST_DATABASE_URL=postgresql://…/mydb — использовать готовую
БД (createdb/dropdb не вызываются, применяются только миграции).
"""
from __future__ import annotations

import os
import re

from dotenv import load_dotenv

load_dotenv()

TEST_DB_NAME = "crm_skyex_test"


def managed_test_database_url() -> tuple[str, bool]:
    """(url, managed): managed=True — БД создаём/удаляем сами через createdb."""
    override = os.getenv("TEST_DATABASE_URL", "").strip()
    if override:
        return override, False
    base_url = os.environ["DATABASE_URL"].rsplit("/", 1)[0]
    return f"{base_url}/{TEST_DB_NAME}", True


def database_user(url: str) -> str:
    match = re.match(r"postgresql://([^:@/]+)", url)
    if not match:
        raise ValueError(f"Не удалось извлечь пользователя из DSN: {url!r}")
    return match.group(1)
