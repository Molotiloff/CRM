from __future__ import annotations

from decimal import Decimal

from db_asyncpg.repositories.base import ConnectionBoundRepo
from db_asyncpg.repositories.transactions import TransactionsRepo
from services.aml.billing import AMLBillingPort


class AMLBillingRepository(ConnectionBoundRepo, AMLBillingPort):
    async def wallet_client(self, chat_id: int) -> int | None:
        async with self._connection() as con:
            value = await con.fetchval(
                """SELECT c.id FROM clients c
                   JOIN client_accounts a ON a.client_id=c.id
                   WHERE c.chat_id=$1 AND c.is_active AND c.closed_at IS NULL
                     AND a.currency_code='USDT' AND a.is_active""",
                chat_id,
            )
        return int(value) if value is not None else None

    async def charge(self, *, client_id: int, chat_id: int, idempotency_key: str) -> Decimal:
        async with self._connection() as con:
            async with con.transaction():
                client = await con.fetchval(
                    """SELECT id FROM clients WHERE id=$1 AND chat_id=$2
                       AND is_active AND closed_at IS NULL FOR UPDATE""",
                    client_id, chat_id,
                )
                if client is None:
                    raise ValueError("Кошелёк клиента недоступен")
                ledger = TransactionsRepo(self._pool, connection=con)
                await ledger.withdraw(
                    client_id=client_id, currency_code="USDT", amount=Decimal("1"),
                    source="tg_bot", comment="AML-проверка: 1 USDT",
                    idempotency_key=idempotency_key,
                )
                transaction = await ledger.get_transaction_by_idempotency_key(
                    client_id=client_id, idempotency_key=idempotency_key,
                )
                assert transaction is not None
                return Decimal(transaction["balance_after"])
