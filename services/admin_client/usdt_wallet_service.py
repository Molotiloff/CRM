from __future__ import annotations

import html

from db_asyncpg.ports.administration import SettingsRepositoryPort

SETTING_KEY = "USDT_WALLET"


class UsdtWalletService:
    def __init__(self, repo: SettingsRepositoryPort) -> None:
        self.repo = repo

    async def build_show_message(self) -> str:
        addr = await self.repo.get_setting(SETTING_KEY)
        if not addr:
            return "USDT-кошелёк пока не задан."
        return (
            f"<code>{html.escape(addr)}</code>"
        )

    async def set_from_text(self, text: str) -> str:
        parts = (text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return "Использование: /setwallet <адрес USDT> (или /setкош <адрес>)"

        addr = parts[1].strip()
        if len(addr) < 26 or len(addr) > 128:
            return "Похоже, адрес некорректный. Проверьте и попробуйте снова."

        await self.repo.set_setting(SETTING_KEY, addr)
        return "✅ USDT-кошелёк обновлён."
