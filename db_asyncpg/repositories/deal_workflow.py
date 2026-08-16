from __future__ import annotations

from decimal import Decimal
from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo


class DealWorkflowRepository(ConnectionBoundRepo):
    async def get_act_balance_check(self, deal_id: int) -> dict[str, Any] | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT erl.request_chat_id,
                       COALESCE((
                           SELECT t.balance_after
                           FROM clients c
                           JOIN client_accounts a ON a.client_id = c.id
                           JOIN transactions t ON t.account_id = a.id
                           WHERE c.chat_id = erl.request_chat_id
                             AND a.currency_code = 'USDT'
                           ORDER BY t.id DESC
                           LIMIT 1
                       ), 0) AS current_amount,
                       COALESCE(SUM(
                           CASE WHEN art.direction = 'OUT' AND a.currency_code = 'USDT'
                                THEN ABS(t.amount) ELSE 0 END
                       ), 0) AS required_amount
                FROM deals d
                JOIN exchange_request_links erl
                  ON erl.client_req_id = d.exchange_client_req_id
                LEFT JOIN act_request_transactions art
                  ON art.req_id = d.exchange_client_req_id
                 AND art.status = 'ACTIVE'
                LEFT JOIN transactions t ON t.id = art.transaction_id
                LEFT JOIN client_accounts a ON a.id = t.account_id
                WHERE d.id = $1
                GROUP BY erl.request_chat_id
                """,
                deal_id,
            )
        if row is None or row["request_chat_id"] is None:
            return None
        current = Decimal(str(row["current_amount"]))
        required = Decimal(str(row["required_amount"]))
        return {
            "request_chat_id": int(row["request_chat_id"]),
            "current_amount": current,
            "required_amount": required,
            "shortage_amount": max(-current, Decimal("0")),
            "insufficient": current < 0,
        }

    async def resolve_payment_watch(
        self,
        *,
        deal_id: int,
        requested_watch_id: int | None,
    ) -> dict[str, Any] | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT pw.*
                FROM deals d
                LEFT JOIN clients c ON c.id = d.client_id
                LEFT JOIN exchange_request_links erl
                  ON erl.client_req_id = d.exchange_client_req_id
                JOIN payment_watches pw
                  ON pw.chat_id = COALESCE(
                      erl.client_chat_id,
                      NULLIF(d.body->>'telegram_client_chat_id', '')::bigint,
                      c.chat_id
                  )
                WHERE d.id = $1
                  AND ($2::bigint IS NULL OR pw.id = $2)
                  AND (pw.deal_id IS NULL OR pw.deal_id = d.id)
                  AND pw.status IN ('WATCHING', 'TIMED_OUT', 'COMPLETED')
                ORDER BY (pw.id = $2) DESC NULLS LAST,
                         (pw.deal_id = d.id) DESC, pw.created_at DESC
                LIMIT 1
                """,
                deal_id,
                requested_watch_id,
            )
        return dict(row) if row else None

    async def get_completed_payment(self, deal_id: int) -> dict[str, Any] | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT pw.id AS watch_id, pwe.tx_hash, pwe.amount,
                       pwe.token_symbol, pwe.confirmations, pwe.block_ts
                FROM payment_watches pw
                JOIN LATERAL (
                    SELECT *
                    FROM payment_watch_events
                    WHERE watch_id = pw.id AND event_type = 'MAIN'
                    ORDER BY block_ts DESC, id DESC
                    LIMIT 1
                ) pwe ON TRUE
                WHERE pw.deal_id = $1 AND pw.status = 'COMPLETED'
                ORDER BY pw.completed_at DESC NULLS LAST, pw.id DESC
                LIMIT 1
                """,
                deal_id,
            )
        return dict(row) if row else None
