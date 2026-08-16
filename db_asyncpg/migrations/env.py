"""Alembic env: async-движок поверх того же asyncpg, что использует приложение.

Схема ведётся raw-SQL миграциями (versions/* исполняют файлы из sql/),
поэтому autogenerate не используется и target_metadata = None.
"""
from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

config = context.config

# Программный запуск (AlembicMigrator) логирование уже настроил — не трогаем его.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = None


def _database_url() -> str:
    url = config.attributes.get("sqlalchemy_url") or ""
    if not url:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "Не найден URL БД: задайте DATABASE_URL в окружении/.env "
            "или attributes['sqlalchemy_url'] при программном запуске"
        )
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def run_migrations_offline() -> None:
    """Режим --sql: рендер SQL без подключения к БД."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(_database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
