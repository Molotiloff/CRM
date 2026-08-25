from __future__ import annotations

from decimal import Decimal
from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo


class DealWorkflowRepository(ConnectionBoundRepo):
    async def get_fulfillment_balance_check(
        self,
        deal_id: int,
    ) -> dict[str, Any] | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                WITH queue AS (
                    SELECT COALESCE(SUM(qty), 0) AS queued_qty
                    FROM usdt_fulfillment_queue
                    WHERE status IN ('queued', 'executing')
                ), fact AS (
                    SELECT COALESCE((
                        SELECT actual_qty
                        FROM firm_wallet_fact_snapshots
                        WHERE currency_code = 'USDT'
                        ORDER BY observed_at DESC, id DESC
                        LIMIT 1
                    ), 0) AS usdt_fact
                )
                SELECT COALESCE(
                           erl.request_chat_id,
                           erl.client_chat_id,
                           c.chat_id
                       ) AS request_chat_id,
                       item.qty AS required_amount,
                       queue.queued_qty,
                       fact.usdt_fact
                FROM deals d
                JOIN usdt_fulfillment_queue item ON item.deal_id = d.id
                LEFT JOIN clients c ON c.id = d.client_id
                LEFT JOIN exchange_request_links erl
                  ON erl.client_req_id = d.exchange_client_req_id
                CROSS JOIN queue
                CROSS JOIN fact
                WHERE d.id = $1
                  AND item.status IN ('queued', 'executing')
                """,
                deal_id,
            )
        if row is None:
            return None
        fact = Decimal(str(row["usdt_fact"]))
        queued = Decimal(str(row["queued_qty"]))
        required = Decimal(str(row["required_amount"]))
        return {
            "request_chat_id": (
                int(row["request_chat_id"])
                if row["request_chat_id"] is not None
                else None
            ),
            "required_amount": required,
            "queued_qty": queued,
            "usdt_fact": fact,
            "shortage_amount": max(queued - fact, Decimal("0")),
            "onchain_liquid_qty": max(fact - queued, Decimal("0")),
            "insufficient": queued > fact,
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
                JOIN deal_settlements ds
                  ON ds.payment_event_id = pwe.id
                 AND ds.review_status IN ('matched', 'resolved')
                WHERE pw.deal_id = $1 AND pw.status = 'COMPLETED'
                ORDER BY pw.completed_at DESC NULLS LAST, pw.id DESC
                LIMIT 1
                """,
                deal_id,
            )
        return dict(row) if row else None
