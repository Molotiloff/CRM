from __future__ import annotations

import json
from decimal import Decimal

from domain import DealStatus
from services.accounting.cash_settlement_models import (
    CashSettlementCommand,
    CashSettlementContext,
    CashSettlementResult,
)

from .base import ConnectionBoundRepo


class CashSettlementRepo(ConnectionBoundRepo):
    async def acquire_request_lock(self, *, request_id: str) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"cash_settlement:{request_id}",
            )

    async def get_request_for_update(
        self,
        *,
        request_id: str,
    ) -> CashSettlementContext | None:
        return await self._context(request_id=request_id, city_chat_id=None)

    async def get_context_for_update(
        self,
        *,
        request_id: str,
        city_chat_id: int,
    ) -> CashSettlementContext | None:
        return await self._context(
            request_id=request_id,
            city_chat_id=city_chat_id,
        )

    async def _context(
        self,
        *,
        request_id: str,
        city_chat_id: int | None,
    ) -> CashSettlementContext | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                SELECT d.id AS deal_id, d.status AS deal_status, d.client_id,
                       client.chat_id AS client_chat_id,
                       client.name AS client_name,
                       d.city, d.body->>'req_id' AS request_id,
                       d.body->>'request_kind' AS request_kind,
                       UPPER(d.body->>'currency') AS currency_code,
                       (d.body->>'amount')::numeric AS expected_qty,
                       COALESCE(client.client_group, '') <> 'internal_wallet'
                           AS track_client_balance,
                       cash.client_id AS cash_client_id,
                       cash.city AS cash_city
                FROM deals d
                JOIN clients client ON client.id = d.client_id
                LEFT JOIN cash_chat_registry cash
                  ON cash.is_active
                 AND cash.cash_currency_codes IS NULL
                 AND LOWER(BTRIM(cash.city)) = LOWER(BTRIM(d.city))
                 AND ($2::bigint IS NULL OR cash.chat_id = $2)
                WHERE d.source_kind = 'cash'
                  AND d.body->>'req_id' = $1
                  AND d.body->>'request_kind' IN ('dep', 'wd')
                FOR UPDATE OF d
                """,
                request_id,
                city_chat_id,
            )
        if row is None or row["cash_client_id"] is None:
            return None
        return CashSettlementContext(
            deal_id=int(row["deal_id"]),
            deal_status=DealStatus(str(row["deal_status"])),
            client_id=int(row["client_id"]),
            client_chat_id=int(row["client_chat_id"]),
            client_name=str(row["client_name"]),
            cash_client_id=int(row["cash_client_id"]),
            request_id=str(row["request_id"]),
            request_kind=str(row["request_kind"]),
            city=str(row["city"]).strip().lower(),
            currency=str(row["currency_code"]),
            expected_qty=Decimal(str(row["expected_qty"])),
            track_client_balance=bool(row["track_client_balance"]),
        )

    async def get_by_request(
        self,
        *,
        request_id: str,
    ) -> CashSettlementResult | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                SELECT settlement.id, settlement.deal_id,
                       deal.client_id, client.chat_id AS client_chat_id,
                       client.name AS client_name,
                       settlement.request_id, settlement.request_kind,
                       settlement.currency_code, settlement.actual_qty,
                       settlement.cash_transaction_id,
                       settlement.client_transaction_id,
                       settlement.position_move_id
                FROM cash_settlements settlement
                JOIN deals deal ON deal.id = settlement.deal_id
                JOIN clients client ON client.id = deal.client_id
                WHERE settlement.request_id = $1
                """,
                request_id,
            )
        return self._result(row, repeated=False) if row is not None else None

    async def insert(
        self,
        *,
        context: CashSettlementContext,
        command: CashSettlementCommand,
        actual_qty: Decimal,
        cash_transaction_id: int,
        client_transaction_id: int | None,
        position_move_id: int | None,
    ) -> CashSettlementResult:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO cash_settlements(
                    deal_id, request_id, request_kind, city, currency_code,
                    expected_qty, actual_qty, command_chat_id, command_message_id,
                    cash_transaction_id, client_transaction_id, position_move_id,
                    evidence, settled_by_tg_user_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        $13::jsonb, $14)
                RETURNING id, deal_id, request_id, request_kind, currency_code,
                          actual_qty, cash_transaction_id, client_transaction_id,
                          position_move_id
                """,
                context.deal_id,
                context.request_id,
                context.request_kind,
                context.city,
                context.currency,
                context.expected_qty,
                actual_qty,
                command.city_chat_id,
                command.command_message_id,
                cash_transaction_id,
                client_transaction_id,
                position_move_id,
                json.dumps(command.evidence),
                command.actor_tg_user_id,
            )
        return self._result(
            row,
            repeated=False,
            client_id=context.client_id,
            client_chat_id=context.client_chat_id,
            client_name=context.client_name,
        )

    @staticmethod
    def _result(
        row,
        *,
        repeated: bool,
        client_id: int | None = None,
        client_chat_id: int | None = None,
        client_name: str | None = None,
    ) -> CashSettlementResult:
        return CashSettlementResult(
            settlement_id=int(row["id"]),
            deal_id=int(row["deal_id"]),
            client_id=(int(row["client_id"]) if client_id is None else client_id),
            client_chat_id=(
                int(row["client_chat_id"])
                if client_chat_id is None
                else client_chat_id
            ),
            client_name=(
                str(row["client_name"]) if client_name is None else client_name
            ),
            request_id=str(row["request_id"]),
            request_kind=str(row["request_kind"]),
            currency=str(row["currency_code"]),
            actual_qty=Decimal(str(row["actual_qty"])),
            cash_transaction_id=int(row["cash_transaction_id"]),
            client_transaction_id=(
                int(row["client_transaction_id"])
                if row["client_transaction_id"] is not None
                else None
            ),
            position_move_id=(
                int(row["position_move_id"])
                if row["position_move_id"] is not None
                else None
            ),
            repeated=repeated,
        )
