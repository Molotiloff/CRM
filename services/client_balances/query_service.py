from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from db_asyncpg.ports.clients import WalletRepositoryPort


@dataclass(slots=True, frozen=True)
class ClientBalanceRow:
    client_id: int
    client_name: str
    chat_id: int | None
    client_group: str
    currency_code: str
    balance: Decimal
    precision: int


class ClientBalancesQueryService:
    def __init__(self, repo: WalletRepositoryPort) -> None:
        self.repo = repo

    async def balances_by_client(self) -> list[ClientBalanceRow]:
        rows = await self.repo.balances_by_client()
        return [
            ClientBalanceRow(
                client_id=int(row["client_id"]),
                client_name=str(row.get("client_name") or ""),
                chat_id=int(row["chat_id"]) if row.get("chat_id") is not None else None,
                client_group=str(row.get("client_group") or ""),
                currency_code=str(row["currency_code"]).upper(),
                balance=Decimal(str(row["balance"])),
                precision=int(row.get("precision", 2)),
            )
            for row in rows
        ]
