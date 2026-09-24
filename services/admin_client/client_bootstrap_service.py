from __future__ import annotations

from db_asyncpg.ports.workflows import ClientWalletRepositoryPort

DEFAULT_CURRENCIES: list[tuple[str, int]] = [
    ("USD", 2),
    ("USDW", 2),
    ("USDT", 2),
    ("RUB", 2),
    ("EUR", 2),
    ("THB", 2),
    ("EUR500", 2),
    ("РУБПЕР", 2),
]


class ClientBootstrapService:
    def __init__(self, repo: ClientWalletRepositoryPort) -> None:
        self.repo = repo

    async def ensure_client_wallet(self, *, chat_id: int, chat_name: str) -> int:
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
        rows = await self.repo.snapshot_wallet(client_id)
        existing_codes = {str(row["currency_code"]).upper() for row in rows}

        for code, precision in DEFAULT_CURRENCIES:
            if code.upper() not in existing_codes:
                await self.repo.add_currency(client_id, code, precision)

        return client_id
