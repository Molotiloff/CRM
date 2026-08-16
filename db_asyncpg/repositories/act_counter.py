from __future__ import annotations

from typing import Any

from db_asyncpg.repositories.base import BaseRepo


class ActCounterRepo(BaseRepo):
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
