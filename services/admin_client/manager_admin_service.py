from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from db_asyncpg.ports.administration import ManagerRepositoryPort


class ManagerAdminService:
    def __init__(self, repo: ManagerRepositoryPort) -> None:
        self.repo = repo

    @staticmethod
    async def _identity(
        manager: dict[str, Any],
        profile_resolver: Callable[[int], Awaitable[dict | None]] | None,
    ) -> str:
        uid = int(manager["user_id"])
        profile = await profile_resolver(uid) if profile_resolver is not None else None
        username = profile.get("username") if profile is not None else manager.get("username")
        username = username.strip().removeprefix("@") if isinstance(username, str) else ""
        if username and re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
            nickname = "@" + username + (" (последний известный)" if profile is None else "")
        else:
            nickname = "ник не указан" if profile is not None else "ник недоступен"
        return f"<code>{uid}</code> — {nickname}"

    async def handle_command(
        self,
        text: str,
        *,
        profile_resolver: Callable[[int], Awaitable[dict | None]] | None = None,
    ) -> str:
        text = (text or "").strip()

        m_add = re.match(r"^/mgr(?:@\w+)?\s*\+\s+(\d+)(?:\s+(.+))?$", text, flags=re.I | re.U)
        if m_add:
            user_id = int(m_add.group(1))
            display_name = (m_add.group(2) or "").strip()
            ok = await self.repo.add_manager(user_id=user_id, display_name=display_name)
            if not ok:
                return "❌ Не удалось добавить менеджера."
            managers = await self.repo.list_managers()
            manager = next(
                (row for row in managers if row["user_id"] == user_id), {"user_id": user_id}
            )
            return "✅ Добавлен менеджер: " + await self._identity(manager, profile_resolver)

        m_del = re.match(r"^/mgr(?:@\w+)?\s*-\s+(\d+)\s*$", text, flags=re.I | re.U)
        if m_del:
            user_id = int(m_del.group(1))
            managers = await self.repo.list_managers()
            manager = next(
                (row for row in managers if row["user_id"] == user_id), {"user_id": user_id}
            )
            ok = await self.repo.remove_manager(user_id=user_id)
            return (
                "✅ Удалён менеджер: " + await self._identity(manager, profile_resolver)
                if ok
                else "❌ Менеджер не найден."
            )

        managers = await self.repo.list_managers()
        if not managers:
            return "Список менеджеров пуст."

        semaphore = asyncio.Semaphore(5)

        async def identity(manager: dict) -> str:
            async with semaphore:
                return await self._identity(manager, profile_resolver)

        identities = await asyncio.gather(*(identity(manager) for manager in managers))
        lines = ["Менеджеры:", *("• " + value for value in identities)]
        return "\n".join(lines)
