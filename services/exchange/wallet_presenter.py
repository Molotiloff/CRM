from __future__ import annotations

import html
from collections.abc import Sequence
from typing import Any

from services.wallets.compact_formatter import format_wallet_compact


class ExchangeWalletPresenter:
    @staticmethod
    def summary(chat_name: str, accounts: Sequence[dict[str, Any]]) -> str:
        compact = format_wallet_compact(accounts, only_nonzero=True)
        if compact == "Пусто":
            return "Все счета нулевые. Посмотреть всё: /кошелек"
        safe_title = html.escape(f"Средств у {chat_name}:")
        return f"<code>{safe_title}\n\n{html.escape(compact)}</code>"
