from __future__ import annotations

import html
import re

from db_asyncpg.ports.clients import ClientRepositoryPort


class ClientGroupService:
    def __init__(self, repo: ClientRepositoryPort) -> None:
        self.repo = repo

    async def assign_from_text(self, text: str) -> str:
        text = (text or "").strip()
        match = re.match(r"^/группа(?:@\w+)?\s+(-?\d+)\s+(.+)$", text, flags=re.IGNORECASE | re.UNICODE)
        if not match:
            return (
                "Использование: /группа <chat_id> <группа>\n"
                "Пример: /группа 123456789 VIP"
            )

        try:
            target_chat_id = int(match.group(1))
        except ValueError:
            return "Некорректный chat_id."

        client_group = match.group(2).strip()
        if not client_group:
            return "Укажите группу после chat_id."

        record = await self.repo.set_client_group_by_chat_id(target_chat_id, client_group)
        if not record:
            return f"Клиент с chat_id={target_chat_id} не найден."

        safe_name = html.escape(record.get("name") or "")
        safe_group = html.escape(record.get("client_group") or "")
        return f"✅ Группа для «{safe_name}» (chat_id={target_chat_id}) установлена: {safe_group}"
