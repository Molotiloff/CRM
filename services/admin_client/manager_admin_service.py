from __future__ import annotations

import html
import re

from db_asyncpg.ports.administration import ManagerRepositoryPort


class ManagerAdminService:
    def __init__(self, repo: ManagerRepositoryPort) -> None:
        self.repo = repo

    async def handle_command(self, text: str) -> str:
        text = (text or "").strip()

        m_add = re.match(r"^/mgr(?:@\w+)?\s*\+\s+(\d+)(?:\s+(.+))?$", text, flags=re.I | re.U)
        if m_add:
            user_id = int(m_add.group(1))
            display_name = (m_add.group(2) or "").strip()
            ok = await self.repo.add_manager(user_id=user_id, display_name=display_name)
            disp = f" — {html.escape(display_name)}" if display_name else ""
            return (
                f"✅ Добавлен менеджер: <code>{user_id}</code>{disp}"
                if ok
                else "❌ Не удалось добавить менеджера."
            )

        m_del = re.match(r"^/mgr(?:@\w+)?\s*-\s+(\d+)\s*$", text, flags=re.I | re.U)
        if m_del:
            user_id = int(m_del.group(1))
            ok = await self.repo.remove_manager(user_id=user_id)
            return (
                f"✅ Удалён менеджер: <code>{user_id}</code>"
                if ok
                else "❌ Менеджер не найден."
            )

        managers = await self.repo.list_managers()
        if not managers:
            return "Список менеджеров пуст."

        lines = ["Менеджеры:"]
        for manager in managers:
            uid = manager["user_id"]
            name = (manager.get("display_name") or "").strip()
            lines.append(f"• <code>{uid}</code>{(' — ' + html.escape(name)) if name else ''}")
        return "\n".join(lines)
