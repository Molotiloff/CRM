from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

from db_asyncpg.repositories.base import ConnectionBoundRepo
from domain import SettlementReviewStatus
from domain.accounting_flows import SettlementResolution
from services.payment_watch.settlement_models import (
    ConfirmedTransfer,
    PaymentEventClaim,
    SettlementContext,
    SettlementResult,
    SettlementReviewContext,
)


class BlockchainEventAlreadyClaimedError(RuntimeError):
    pass


class DealSettlementRepo(ConnectionBoundRepo):
    async def get_context_for_update(self, *, watch_id: int) -> SettlementContext | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT d.id AS deal_id, d.status AS deal_status, d.client_id,
                       c.chat_id AS client_chat_id, erl.request_chat_id,
                       d.body->>'recv_code' AS recv_code,
                       d.body->>'recv_amount' AS recv_amount,
                       d.body->>'pay_code' AS pay_code,
                       d.body->>'pay_amount' AS pay_amount
                FROM payment_watches pw
                JOIN deals d ON d.id = pw.deal_id
                LEFT JOIN clients c ON c.id = d.client_id
                LEFT JOIN exchange_request_links erl
                  ON erl.client_req_id = d.exchange_client_req_id
                WHERE pw.id = $1
                FOR UPDATE OF pw, d
                """,
                watch_id,
            )
        if row is None:
            return None
        return SettlementContext(
            deal_id=int(row["deal_id"]),
            deal_status=str(row["deal_status"]),
            client_id=int(row["client_id"]),
            client_chat_id=(
                int(row["client_chat_id"]) if row["client_chat_id"] is not None else None
            ),
            request_chat_id=(
                int(row["request_chat_id"]) if row["request_chat_id"] is not None else None
            ),
            recv_code=str(row["recv_code"] or "").upper(),
            recv_amount=Decimal(str(row["recv_amount"])),
            pay_code=str(row["pay_code"] or "").upper(),
            pay_amount=Decimal(str(row["pay_amount"])),
        )

    async def claim_main_event(self, transfer: ConfirmedTransfer) -> PaymentEventClaim:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                INSERT INTO payment_watch_events (
                    watch_id, tx_hash, event_type, direction, amount,
                    token_symbol, confirmations, block_ts
                )
                VALUES ($1, $2, 'MAIN', $3, $4, $5, $6, $7)
                ON CONFLICT (tx_hash) DO NOTHING
                RETURNING id, watch_id
                """,
                transfer.watch_id,
                transfer.tx_hash,
                transfer.direction,
                transfer.amount,
                transfer.token_symbol.upper(),
                transfer.confirmations,
                transfer.block_ts,
            )
            if row is not None:
                return PaymentEventClaim(int(row["id"]), int(row["watch_id"]), True)
            existing = await con.fetchrow(
                "SELECT id, watch_id FROM payment_watch_events WHERE tx_hash = $1",
                transfer.tx_hash,
            )
        if existing is None:
            raise RuntimeError("Blockchain event conflict could not be read")
        if int(existing["watch_id"]) != transfer.watch_id:
            raise BlockchainEventAlreadyClaimedError(
                f"Blockchain event {transfer.tx_hash} belongs to another payment watch"
            )
        return PaymentEventClaim(int(existing["id"]), int(existing["watch_id"]), False)

    async def get_by_event(self, *, event_id: int) -> SettlementResult | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT ds.id, ds.payment_event_id, ds.deal_id, ds.expected_qty,
                       ds.actual_qty, ds.delta_qty, ds.review_status, ds.resolution,
                       ds.currency_code,
                       ds.evidence::text AS evidence,
                       pwe.watch_id, pwe.tx_hash, pwe.direction AS event_direction,
                       pwe.amount AS event_amount, pwe.token_symbol,
                       pwe.confirmations, pwe.block_ts
                FROM deal_settlements ds
                JOIN payment_watch_events pwe ON pwe.id = ds.payment_event_id
                WHERE ds.payment_event_id = $1
                """,
                event_id,
            )
        return _result(row, created=False) if row is not None else None

    async def create(
        self,
        *,
        context: SettlementContext,
        transfer: ConfirmedTransfer,
        event_id: int,
        expected: Decimal,
        actual: Decimal,
        review_status: str,
    ) -> SettlementResult:
        direction = "outgoing" if transfer.direction == "IN" else "incoming"
        evidence = {
            "tx_hash": transfer.tx_hash,
            "from_address": transfer.from_address,
            "to_address": transfer.to_address,
            "confirmations": transfer.confirmations,
            "block_ts": transfer.block_ts.isoformat(),
        }
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                INSERT INTO deal_settlements (
                    deal_id, payment_event_id, direction, currency_code,
                    expected_qty, actual_qty, evidence, review_status, idempotency_key
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
                ON CONFLICT (payment_event_id) WHERE payment_event_id IS NOT NULL
                DO NOTHING
                RETURNING id, payment_event_id, deal_id, expected_qty, actual_qty,
                          delta_qty, review_status
                """,
                context.deal_id,
                event_id,
                direction,
                transfer.token_symbol.upper(),
                expected,
                actual,
                json.dumps(evidence),
                review_status,
                f"payment_event:{event_id}",
            )
        if row is None:
            existing = await self.get_by_event(event_id=event_id)
            if existing is None:
                raise RuntimeError("Settlement conflict could not be read")
            return existing
        stored = await self.get_by_event(event_id=event_id)
        if stored is None:
            raise RuntimeError("Created settlement could not be read back")
        return replace(stored, created=True)

    async def complete_watch(self, *, watch_id: int) -> None:
        async with self._connection() as con:
            await con.execute(
                """
                UPDATE payment_watches
                SET status = 'COMPLETED', completed_at = COALESCE(completed_at, NOW())
                WHERE id = $1
                """,
                watch_id,
            )

    async def enqueue_review_notification(
        self,
        *,
        context: SettlementContext,
        result: SettlementResult,
        tx_hash: str,
    ) -> None:
        chat_ids = list(
            dict.fromkeys(
                chat_id
                for chat_id in (context.client_chat_id, context.request_chat_id)
                if chat_id is not None
            )
        )
        payload = {
            "dealId": context.deal_id,
            "chatIds": chat_ids,
            "expected": str(result.expected),
            "actual": str(result.actual),
            "delta": str(result.delta),
            "txHash": tx_hash,
        }
        async with self._connection() as con:
            await con.execute(
                """
                INSERT INTO tg_outbox (kind, payload, dedup_key)
                VALUES ('settlement_needs_review', $1::jsonb, $2)
                ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
                """,
                json.dumps(payload),
                f"settlement:{result.settlement_id}:needs_review",
            )

    async def get_review_for_update(
        self,
        *,
        settlement_id: int,
    ) -> SettlementReviewContext | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT ds.id, ds.payment_event_id, ds.deal_id, ds.expected_qty,
                       ds.actual_qty, ds.delta_qty, ds.currency_code,
                       ds.review_status, ds.resolution,
                       ds.evidence::text AS evidence,
                       pwe.watch_id, pwe.tx_hash, pwe.direction AS event_direction,
                       pwe.amount AS event_amount, pwe.token_symbol,
                       pwe.confirmations, pwe.block_ts,
                       d.status AS deal_status, d.client_id, d.city, d.source_ref,
                       d.source_kind, d.body::text AS body,
                       COALESCE(erl.client_req_id, d.source_ref) AS client_req_id,
                       COALESCE(erl.table_req_id, d.source_ref) AS table_req_id,
                       COALESCE(erl.table_in_cur, d.body->>'recv_code') AS table_in_cur,
                       COALESCE(erl.table_in_amount,
                                NULLIF(d.body->>'recv_amount', '')::numeric)
                           AS table_in_amount,
                       COALESCE(erl.table_out_cur, d.body->>'pay_code') AS table_out_cur,
                       COALESCE(erl.table_out_amount,
                                NULLIF(d.body->>'pay_amount', '')::numeric)
                           AS table_out_amount,
                       COALESCE(erl.table_rate,
                                NULLIF(d.body->>'rate', '')::numeric,
                                1) AS table_rate
                FROM deal_settlements ds
                JOIN payment_watch_events pwe ON pwe.id = ds.payment_event_id
                JOIN deals d ON d.id = ds.deal_id
                LEFT JOIN exchange_request_links erl
                  ON erl.client_req_id = d.exchange_client_req_id
                WHERE ds.id = $1
                FOR UPDATE OF ds, d
                """,
                settlement_id,
            )
        if row is None:
            return None
        result = _result(row, created=False)
        return SettlementReviewContext(
            result=result,
            event_direction=str(row["event_direction"]),
            settlement_currency=str(row["currency_code"]).upper(),
            is_exchange=str(row["source_kind"] or "") == "exchange",
            deal_status=str(row["deal_status"]),
            client_id=int(row["client_id"]),
            city=str(row["city"]),
            source_ref=str(row["source_ref"] or f"deal:{row['deal_id']}"),
            request_id=str(row["client_req_id"]),
            table_request_id=str(row["table_req_id"]),
            recv_code=str(row["table_in_cur"]).upper(),
            recv_amount=Decimal(str(row["table_in_amount"])),
            pay_code=str(row["table_out_cur"]).upper(),
            pay_amount=Decimal(str(row["table_out_amount"])),
            rate=Decimal(str(row["table_rate"])),
            body=json.loads(row["body"] or "{}"),
        )

    async def amend_contract_to_actual(self, context: SettlementReviewContext) -> None:
        leg = _review_leg(context)
        amount_column = "table_in_amount" if leg == "recv" else "table_out_amount"
        body_key = f"{leg}_amount"
        async with self._connection() as con:
            if context.is_exchange:
                await con.execute(
                    f"""
                    UPDATE exchange_request_links
                    SET {amount_column} = $2, updated_at = NOW()
                    WHERE client_req_id = $1
                    """,
                    context.request_id,
                    context.result.actual,
                )
            await con.execute(
                """
                UPDATE deals
                SET body = jsonb_set(body, ARRAY[$2::text], to_jsonb($3::text), true)
                WHERE id = $1
                """,
                context.result.deal_id,
                body_key,
                str(context.result.actual),
            )

    async def resolve_review(
        self,
        *,
        settlement_id: int,
        resolution: str,
        actor_user_id: int,
        comment: str | None,
    ) -> SettlementResult:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                UPDATE deal_settlements
                SET review_status = 'resolved', resolution = $2,
                    resolved_by = $3, resolved_at = NOW(), comment = $4,
                    updated_at = NOW()
                WHERE id = $1 AND review_status = 'needs_review'
                RETURNING id, payment_event_id, deal_id, expected_qty, actual_qty,
                          delta_qty, review_status, resolution
                """,
                settlement_id,
                resolution,
                actor_user_id,
                comment,
            )
        if row is None:
            raise RuntimeError("Settlement review is no longer pending")
        return _result(row, created=False)


def _result(row, *, created: bool) -> SettlementResult:
    raw_resolution = row.get("resolution")
    raw_evidence = row.get("evidence")
    evidence_data = json.loads(raw_evidence) if raw_evidence else None
    evidence = None
    if evidence_data is not None and row.get("tx_hash") is not None:
        evidence = ConfirmedTransfer(
            watch_id=int(row["watch_id"]),
            tx_hash=str(row["tx_hash"]),
            direction=str(row["event_direction"]),
            amount=Decimal(str(row["event_amount"])),
            token_symbol=str(row["token_symbol"]),
            confirmations=int(row["confirmations"]),
            block_ts=row["block_ts"],
            from_address=str(evidence_data["from_address"]),
            to_address=str(evidence_data["to_address"]),
        )
    return SettlementResult(
        settlement_id=int(row["id"]),
        event_id=int(row["payment_event_id"]),
        deal_id=int(row["deal_id"]),
        expected=Decimal(str(row["expected_qty"])),
        actual=Decimal(str(row["actual_qty"])),
        delta=Decimal(str(row["delta_qty"])),
        status=SettlementReviewStatus(str(row["review_status"])),
        created=created,
        resolution=(
            SettlementResolution(str(raw_resolution)) if raw_resolution is not None else None
        ),
        evidence=evidence,
    )


def _review_leg(context: SettlementReviewContext) -> str:
    recv_matches = context.recv_code == context.settlement_currency
    pay_matches = context.pay_code == context.settlement_currency
    if recv_matches != pay_matches:
        return "recv" if recv_matches else "pay"
    return "pay" if context.event_direction == "IN" else "recv"
