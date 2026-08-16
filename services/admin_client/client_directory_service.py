from __future__ import annotations

import html

from db_asyncpg.ports.clients import ClientRepositoryPort


class ClientDirectoryService:
    def __init__(self, repo: ClientRepositoryPort) -> None:
        self.repo = repo

    @staticmethod
    def _chunk(text: str, limit: int = 3500) -> list[str]:
        out, cur, total = [], [], 0
        for line in text.splitlines(True):
            if total + len(line) > limit and cur:
                out.append("".join(cur))
                cur, total = [], 0
            cur.append(line)
            total += len(line)
        if cur:
            out.append("".join(cur))
        return out

    @staticmethod
    def parse_rmclient_chat_id(text: str) -> int | None:
        parts = (text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return None
        try:
            return int(parts[1].strip())
        except ValueError:
            return -1

    async def build_clients_chunks(self, *, group: str | None = None) -> list[str]:
        if group:
            clients = await self.repo.list_clients_by_group(group)
            title = f"<b>Клиенты группы «{html.escape(group)}»: {len(clients)}</b>"
        else:
            clients = await self.repo.list_clients()
            title = f"<b>Клиенты: {len(clients)}</b>"

        if not clients:
            if group:
                return [f"В группе «{html.escape(group)}» нет активных клиентов."]
            return ["Нет активных клиентов."]

        lines: list[str] = [title]
        for client in sorted(clients, key=lambda item: (item.get("name") or "").lower()):
            name = html.escape(client.get("name") or "")
            client_group = html.escape(client.get("client_group") or "")
            chat_id = client["chat_id"]

            line = f"{name}"
            if client_group:
                line += f" — {client_group}"
            line += f"\n    chat_id = <code>{chat_id}</code>"
            lines.append(line)

        return self._chunk("\n".join(lines))

    @staticmethod
    def build_remove_confirmation(chat_id: int) -> str:
        return (
            "Подтвердите удаление клиента (мягкое — is_active=false).\n"
            f"chat_id = <code>{chat_id}</code>"
        )

    async def confirm_remove(self, chat_id: int) -> str:
        ok = await self.repo.remove_client(chat_id)
        if ok:
            return (
                "Клиент помечен как неактивный (is_active=false).\n"
                f"chat_id = <code>{chat_id}</code>"
            )
        return (
            "Клиент не найден или уже неактивен.\n"
            f"chat_id = <code>{chat_id}</code>"
        )
