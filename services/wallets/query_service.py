from __future__ import annotations

from db_asyncpg.ports.workflows import ClientWalletRepositoryPort
from services.wallets.text_builder import WalletTextBuilder


class WalletQueryService:
    def __init__(
        self, *, repo: ClientWalletRepositoryPort, text_builder: WalletTextBuilder | None = None
    ) -> None:
        self.repo = repo
        self.text_builder = text_builder or WalletTextBuilder()

    async def build_wallet_text(self, *, chat_id: int, chat_name: str) -> str:
        client_id = await self.repo.ensure_client(chat_id, chat_name)
        rows = await self.repo.snapshot_wallet(client_id)
        return self.text_builder.wallet_text(chat_name=chat_name, rows=rows)
