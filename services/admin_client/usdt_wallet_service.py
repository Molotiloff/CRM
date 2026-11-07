from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Literal

from db_asyncpg.ports.administration import SettingsRepositoryPort

SETTING_KEY = "USDT_WALLET"
SECOND_SETTING_KEY = "USDT_WALLET_2"
THAI_SETTING_KEY = "USDT_WALLET_THAI"
WalletSlot = Literal[1, 2, "thai"]


@dataclass(frozen=True, slots=True)
class _WalletConfig:
    setting_key: str
    missing_message: str
    usage: str
    updated_message: str


_WALLETS: dict[WalletSlot, _WalletConfig] = {
    1: _WalletConfig(SETTING_KEY, "USDT-кошелёк пока не задан.",
        "Использование: /setwallet <адрес USDT> (или /setкош <адрес>)", "✅ USDT-кошелёк обновлён."),
    2: _WalletConfig(SECOND_SETTING_KEY, "Второй USDT-кошелёк пока не задан.",
        "Использование: /setwallet2 <адрес USDT>", "✅ Второй USDT-кошелёк обновлён."),
    "thai": _WalletConfig(THAI_SETTING_KEY, "Тайский USDT-кошелёк пока не задан.",
        "Использование: /setwalletthai <адрес USDT> (или /setwallett <адрес USDT>)", "✅ Тайский USDT-кошелёк обновлён."),
}


class UsdtWalletService:
    def __init__(self, repo: SettingsRepositoryPort) -> None:
        self.repo = repo

    async def build_show_message(self, *, wallet_slot: WalletSlot = 1) -> str:
        addr = await self.repo.get_setting(self._setting_key(wallet_slot))
        if not addr:
            return _WALLETS[wallet_slot].missing_message
        return (
            f"<code>{html.escape(addr)}</code>"
        )

    async def set_from_text(self, text: str, *, wallet_slot: WalletSlot = 1) -> str:
        parts = (text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return _WALLETS[wallet_slot].usage

        addr = parts[1].strip()
        if len(addr) < 26 or len(addr) > 128:
            return "Похоже, адрес некорректный. Проверьте и попробуйте снова."

        await self.repo.set_setting(self._setting_key(wallet_slot), addr)
        return _WALLETS[wallet_slot].updated_message

    @staticmethod
    def _setting_key(wallet_slot: WalletSlot) -> str:
        return _WALLETS[wallet_slot].setting_key
