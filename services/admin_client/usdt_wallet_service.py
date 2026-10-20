from __future__ import annotations

import html
from typing import Literal

from db_asyncpg.ports.administration import SettingsRepositoryPort

SETTING_KEY = "USDT_WALLET"
SECOND_SETTING_KEY = "USDT_WALLET_2"
WalletSlot = Literal[1, 2]


class UsdtWalletService:
    def __init__(self, repo: SettingsRepositoryPort) -> None:
        self.repo = repo

    async def build_show_message(self, *, wallet_slot: WalletSlot = 1) -> str:
        addr = await self.repo.get_setting(self._setting_key(wallet_slot))
        if not addr:
            return "Второй USDT-кошелёк пока не задан." if wallet_slot == 2 else "USDT-кошелёк пока не задан."
        return (
            f"<code>{html.escape(addr)}</code>"
        )

    async def set_from_text(self, text: str, *, wallet_slot: WalletSlot = 1) -> str:
        parts = (text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            if wallet_slot == 2:
                return "Использование: /setwallet2 <адрес USDT>"
            return "Использование: /setwallet <адрес USDT> (или /setкош <адрес>)"

        addr = parts[1].strip()
        if len(addr) < 26 or len(addr) > 128:
            return "Похоже, адрес некорректный. Проверьте и попробуйте снова."

        await self.repo.set_setting(self._setting_key(wallet_slot), addr)
        return "✅ Второй USDT-кошелёк обновлён." if wallet_slot == 2 else "✅ USDT-кошелёк обновлён."

    @staticmethod
    def _setting_key(wallet_slot: WalletSlot) -> str:
        return SECOND_SETTING_KEY if wallet_slot == 2 else SETTING_KEY
