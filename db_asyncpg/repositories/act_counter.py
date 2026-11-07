from __future__ import annotations

from decimal import Decimal
from typing import Any

from db_asyncpg.repositories.base import BaseRepo
from db_asyncpg.repositories.transactions import TransactionsRepo
from domain import DomainStateError, DomainValidationError
from services.act_counter.models import ActInvoicePosting


class ActCounterRepo(BaseRepo):
    async def apply_invoice_profit(
        self, *, request_chat_id: int, invoice_client_id: int, deal_id: int,
        delta: Decimal, actor_user_id: int,
    ) -> ActInvoicePosting:
        """Post the invoice's net movement to the same account read by /акт."""
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """SELECT c.id AS client_id, a.id AS account_id, a.balance, a.precision
                       FROM clients c JOIN client_accounts a ON a.client_id=c.id
                       WHERE c.chat_id=$1 AND c.is_active AND c.closed_at IS NULL
                         AND a.currency_code='USDT' AND a.is_active
                       FOR UPDATE OF c, a""", request_chat_id,
                )
                if row is None:
                    raise DomainStateError("Сначала выполните /акт в настроенном чате заявок, чтобы открыть счёт ACT")
                if int(row["client_id"]) == invoice_client_id:
                    raise DomainValidationError("Чат заявок ACT нельзя выбирать клиентом инвойса")
                before = Decimal(str(row["balance"]))
                after = before + delta
                if after < 0:
                    raise DomainStateError("Недостаточно USDT по /акт для покрытия убытка")
                # ACT is an operational stock account, not customer funds.
                await con.execute("UPDATE clients SET client_group='internal_wallet' WHERE id=$1 AND client_group IS DISTINCT FROM 'internal_wallet'", row["client_id"])
                # Preserve invoice precision instead of silently rounding at legacy ACT precision=3.
                if int(row["precision"]) < 8:
                    await con.execute("UPDATE client_accounts SET precision=8 WHERE id=$1", row["account_id"])
                transaction_id = None
                if delta:
                    transactions = TransactionsRepo(self._pool, connection=con)
                    operation = transactions.deposit if delta > 0 else transactions.withdraw
                    transaction_id = await operation(client_id=int(row["client_id"]), currency_code="USDT", amount=abs(delta),
                        source="invoice", comment=f"ACT: прибыль/убыток инвойса {deal_id}; CRM user {actor_user_id}", idempotency_key=f"invoice:{deal_id}:act")
                return ActInvoicePosting(client_id=int(row["client_id"]), account_id=int(row["account_id"]), before=before, after=after, transaction_id=transaction_id)

    async def link_act_request_transaction(
        self,
        *,
        req_id: str,
        request_chat_id: int,
        request_message_id: int,
        transaction_id: int,
        direction: str,
        table_req_id: str | None = None,
        status: str = "ACTIVE",
    ) -> int:
        direction_norm = str(direction).strip().upper()
        status_norm = str(status).strip().upper()

        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    INSERT INTO act_request_transactions (
                        req_id,
                        table_req_id,
                        request_chat_id,
                        request_message_id,
                        transaction_id,
                        direction,
                        status
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (transaction_id) DO UPDATE SET
                        req_id = EXCLUDED.req_id,
                        table_req_id = COALESCE(EXCLUDED.table_req_id, act_request_transactions.table_req_id),
                        request_chat_id = EXCLUDED.request_chat_id,
                        request_message_id = EXCLUDED.request_message_id,
                        direction = EXCLUDED.direction,
                        status = EXCLUDED.status,
                        canceled_at = CASE
                            WHEN EXCLUDED.status = 'CANCELED' THEN now()
                            ELSE NULL
                        END
                    RETURNING id
                    """,
                    str(req_id),
                    str(table_req_id) if table_req_id is not None else None,
                    int(request_chat_id),
                    int(request_message_id),
                    int(transaction_id),
                    direction_norm,
                    status_norm,
                )
                return int(row["id"])

    async def cancel_act_request_transactions(self, *, req_id: str) -> int:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE act_request_transactions
                    SET status = 'CANCELED',
                        canceled_at = now()
                    WHERE req_id = $1
                      AND status <> 'CANCELED'
                    """,
                    str(req_id),
                )
                return int(res.split()[-1])

    async def get_act_request_transaction(self, *, req_id: str) -> list[dict[str, Any]]:
        async with self._connection() as con:
            async with con.transaction():
                rows = await con.fetch(
                    """
                    SELECT
                        art.*,
                        t.client_id,
                        t.account_id,
                        t.txn_at,
                        t.amount,
                        t.balance_after,
                        t.comment,
                        t.source,
                        a.currency_code,
                        a.precision
                    FROM act_request_transactions art
                    JOIN transactions t ON t.id = art.transaction_id
                    JOIN client_accounts a ON a.id = t.account_id
                    WHERE art.req_id = $1
                    ORDER BY art.id ASC
                    """,
                    str(req_id),
                )
                return [dict(row) for row in rows]
