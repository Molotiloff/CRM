"""Программное применение миграций Alembic при старте приложения.

Схема БД версионируется только миграциями (workflow_crm.md C0.1):
ленивые _ensure_*_table() удалены, единственный способ изменить схему —
ревизия в db_asyncpg/migrations/versions/.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AlembicMigrator:
    """Доводит схему БД до head перед запуском бота/API."""

    def __init__(self, database_url: str, project_root: Path = _PROJECT_ROOT) -> None:
        self._database_url = database_url
        self._project_root = project_root

    async def upgrade_to_head(self) -> None:
        # env.py поднимает собственный event loop (asyncio.run) — уводим в поток.
        await asyncio.to_thread(self._upgrade_sync)
        log.info("Alembic: схема БД на актуальной ревизии (head)")

    def _upgrade_sync(self) -> None:
        command.upgrade(self._build_config(), "head")

    def _build_config(self) -> AlembicConfig:
        config = AlembicConfig(str(self._project_root / "alembic.ini"))
        config.set_main_option(
            "script_location", str(self._project_root / "db_asyncpg" / "migrations")
        )
        # URL через attributes, а не set_main_option: конфиг-интерполяция
        # ломается на '%' в пароле; логирование уже настроено приложением.
        config.attributes["sqlalchemy_url"] = self._database_url
        config.attributes["configure_logger"] = False
        return config
