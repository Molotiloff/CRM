from __future__ import annotations

import json
from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo
from domain import Deal, DomainStateError, DomainValidationError
from services.crm.deal_service import (
    DealCreateCommand,
    DealListFilter,
    DealStatusCommand,
    DealStatusConflictError,
    DealUpdateCommand,
)


class DealRepository(ConnectionBoundRepo):
    async def list_deals(self, filters: DealListFilter) -> list[Deal]:
        async with self._connection() as con:
            rows = await con.fetch(
                f"""
                {_DEAL_SELECT}
                WHERE ($1::text[] IS NULL OR d.status = ANY($1::text[]))
                  AND ($2::text IS NULL OR LOWER(d.city) = LOWER($2))
                  AND ($3::text IS NULL OR d.deal_type = $3)
                  AND ($4::bigint IS NULL OR d.client_id = $4)
                  AND ($5::date IS NULL OR d.deal_at >= $5)
                  AND ($6::date IS NULL OR d.deal_at <= $6)
                ORDER BY d.updated_at DESC, d.id DESC
                LIMIT $7 OFFSET $8
                """,
                [str(item) for item in filters.statuses] or None,
                _clean(str(filters.city)) if filters.city is not None else None,
                str(filters.deal_type) if filters.deal_type is not None else None,
                filters.client_id,
                filters.date_from,
                filters.date_to,
                filters.limit,
                filters.offset,
            )
        return [Deal.from_record(_normalize_deal_row(dict(row))) for row in rows]

    async def get_deal(self, deal_id: int) -> Deal | None:
        async with self._connection() as con:
            row = await con.fetchrow(f"{_DEAL_SELECT} WHERE d.id = $1", deal_id)
            if row is None:
                return None
            deal = _normalize_deal_row(dict(row))
            deal["legs"] = [
                dict(item)
                for item in await con.fetch(
                    """
                    SELECT id, direction, currency_code, amount, rate, status
                    FROM deal_legs WHERE deal_id = $1 ORDER BY id
                    """,
                    deal_id,
                )
            ]
            deal["status_events"] = [
                _normalize_status_event(dict(item))
                for item in await con.fetch(
                    """
                    SELECT e.id, e.old_status, e.new_status, e.payload, e.created_at,
                           COALESCE(u.display_name, 'System') AS actor_name
                    FROM deal_status_events e
                    LEFT JOIN users u ON u.id = e.actor_user_id
                    WHERE e.deal_id = $1 ORDER BY e.id
                    """,
                    deal_id,
                )
            ]
        return Deal.from_record(deal)

    async def create_deal(self, command: DealCreateCommand) -> Deal:
        deal_id, _ = await self._insert_deal(command, idempotent=False)
        return await self._require_deal(deal_id)

    async def create_deal_idempotent(
        self, command: DealCreateCommand
    ) -> tuple[Deal, bool]:
        if not command.source_kind or not command.source_ref:
            raise DomainValidationError("source_kind and source_ref are required")
        deal_id, created = await self._insert_deal(command, idempotent=True)
        return await self._require_deal(deal_id), created

    async def _insert_deal(
        self, command: DealCreateCommand, *, idempotent: bool
    ) -> tuple[int, bool]:
        async with self._connection() as con:
            async with con.transaction():
                conflict_clause = (
                    "ON CONFLICT (source, source_kind, source_ref) "
                    "WHERE source_ref IS NOT NULL DO NOTHING"
                    if idempotent
                    else ""
                )
                deal_id = await con.fetchval(
                    f"""
                    INSERT INTO deals (
                        deal_type, city, client_id, counterparty_id, created_by, source,
                        comment, tronscan_url, body, profit, deal_at, exchange_client_req_id,
                        source_kind, source_ref
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10,
                            COALESCE($11, CURRENT_DATE), $12, $13, $14)
                    {conflict_clause}
                    RETURNING id
                    """,
                    command.deal_type,
                    str(command.city),
                    command.client_id,
                    command.counterparty_id,
                    command.actor_user_id,
                    command.source,
                    command.comment,
                    command.tronscan_url,
                    json.dumps(command.body.to_dict()),
                    command.profit,
                    command.deal_at,
                    command.exchange_client_req_id,
                    command.source_kind,
                    command.source_ref,
                )
                created = deal_id is not None
                if deal_id is None:
                    deal_id = await con.fetchval(
                        """
                        SELECT id FROM deals
                        WHERE source = $1 AND source_kind = $2 AND source_ref = $3
                        """,
                        command.source,
                        command.source_kind,
                        command.source_ref,
                    )
                    if deal_id is None:
                        raise DomainStateError(
                            "Idempotent deal could not be read after conflict"
                        )
                    return int(deal_id), False
                await con.execute(
                    """
                    INSERT INTO deal_status_events (deal_id, old_status, new_status, actor_user_id)
                    VALUES ($1, NULL, 'new', $2)
                    """,
                    deal_id,
                    command.actor_user_id,
                )
        return int(deal_id), created

    async def _require_deal(self, deal_id: int) -> Deal:
        deal = await self.get_deal(deal_id)
        if deal is None:
            raise DomainStateError("Created deal could not be read back")
        return deal

    async def update_deal(
        self, deal_id: int, command: DealUpdateCommand
    ) -> Deal | None:
        changes = command.to_changes()
        if not changes:
            return await self.get_deal(deal_id)
        columns = {
            "city": "city",
            "client_id": "client_id",
            "counterparty_id": "counterparty_id",
            "comment": "comment",
            "tronscan_url": "tronscan_url",
            "body": "body",
            "profit": "profit",
            "deal_at": "deal_at",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for field, value in changes.items():
            column = columns[field]
            values.append(json.dumps(value) if field == "body" else value)
            cast = "::jsonb" if field == "body" else ""
            assignments.append(f"{column} = ${len(values)}{cast}")
        values.append(deal_id)
        async with self._connection() as con:
            async with con.transaction():
                updated = await con.fetchrow(
                    f"UPDATE deals SET {', '.join(assignments)} "
                    f"WHERE id = ${len(values)} RETURNING id, source, status",
                    *values,
                )
                if updated is not None and updated["source"] == "tg_bot":
                    await con.execute(
                        """
                        INSERT INTO tg_outbox (kind, payload)
                        VALUES (
                            'deal_source_updated',
                            jsonb_build_object(
                                'dealId', $1::bigint,
                                'status', $2::text
                            )
                        )
                        """,
                        int(updated["id"]),
                        str(updated["status"]),
                    )
        return await self.get_deal(deal_id) if updated is not None else None

    async def change_status(
        self,
        deal_id: int,
        command: DealStatusCommand,
    ) -> tuple[Deal, bool] | None:
        new_status = str(command.status)
        expected_old_status = (
            str(command.expected_old_status)
            if command.expected_old_status is not None
            else None
        )
        payload = command.payload.to_dict()
        body_patch = command.body_patch.to_dict()
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT status, source FROM deals WHERE id = $1 FOR UPDATE", deal_id
                )
                if row is None:
                    return None
                old_status = str(row["status"])
                if expected_old_status is not None and old_status != expected_old_status:
                    raise DealStatusConflictError(
                        f"Deal status changed concurrently: expected {expected_old_status}, got {old_status}"
                    )
                changed = old_status != new_status
                if changed:
                    if command.payment_watch_id is not None:
                        bound = await con.execute(
                            """
                            UPDATE payment_watches
                            SET deal_id = $1
                            WHERE id = $2 AND (deal_id IS NULL OR deal_id = $1)
                            """,
                            deal_id,
                            command.payment_watch_id,
                        )
                        if bound.endswith(" 0"):
                            raise DealStatusConflictError(
                                "Payment watch "
                                f"{command.payment_watch_id} is already linked to another deal"
                            )
                    await con.execute(
                        """
                        UPDATE deals
                        SET status = $2,
                            body = body || $3::jsonb,
                            tronscan_url = COALESCE($4, tronscan_url)
                        WHERE id = $1
                        """,
                        deal_id,
                        new_status,
                        json.dumps(body_patch),
                        command.tronscan_url,
                    )
                    status_event_id = await con.fetchval(
                        """
                        INSERT INTO deal_status_events (
                            deal_id, old_status, new_status, actor_user_id, payload
                        ) VALUES ($1, $2, $3, $4, $5::jsonb)
                        RETURNING id
                        """,
                        deal_id,
                        old_status,
                        new_status,
                        command.actor_user_id,
                        json.dumps(payload),
                    )
                    if row["source"] == "tg_bot":
                        outbox_payload = {
                            "dealId": deal_id,
                            "statusEventId": int(status_event_id),
                            "oldStatus": old_status,
                            "newStatus": new_status,
                            "eventPayload": payload,
                        }
                        await con.execute(
                            """
                            INSERT INTO tg_outbox (kind, payload, dedup_key)
                            VALUES ('deal_status_changed', $1::jsonb, $2)
                            ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
                            """,
                            json.dumps(outbox_payload),
                            f"deal:{deal_id}:status_event:{status_event_id}",
                        )
        deal = await self.get_deal(deal_id)
        return (deal, changed) if deal is not None else None


_DEAL_SELECT = """
SELECT d.*, c.name AS client_name, c.chat_id AS client_chat_id,
       cp.name AS counterparty_name,
       cp.default_fee_percent AS counterparty_percent,
       COALESCE(u.display_name, 'System') AS created_by_name,
       pw.id AS payment_watch_id, pw.status AS payment_watch_status,
       corrected_from.original_deal_id AS corrected_from_deal_id,
       corrected_to.replacement_deal_id AS corrected_to_deal_id,
       COALESCE(corrected_from.reason, corrected_to.reason) AS correction_reason,
       correction_actor.display_name AS correction_actor_name
FROM deals d
LEFT JOIN clients c ON c.id = d.client_id
LEFT JOIN counterparties cp ON cp.id = d.counterparty_id
LEFT JOIN users u ON u.id = d.created_by
LEFT JOIN best_change_deal_corrections corrected_from
  ON corrected_from.replacement_deal_id = d.id
LEFT JOIN best_change_deal_corrections corrected_to
  ON corrected_to.original_deal_id = d.id
LEFT JOIN users correction_actor
  ON correction_actor.id = COALESCE(
      corrected_from.actor_user_id,
      corrected_to.actor_user_id
  )
LEFT JOIN LATERAL (
    SELECT id, status
    FROM payment_watches
    WHERE deal_id = d.id
    ORDER BY created_at DESC, id DESC
    LIMIT 1
) pw ON TRUE
"""


def _clean(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _normalize_deal_row(row: dict[str, Any]) -> dict[str, Any]:
    if isinstance(row.get("body"), str):
        row["body"] = json.loads(row["body"])
    return row


def _normalize_status_event(row: dict[str, Any]) -> dict[str, Any]:
    if isinstance(row.get("payload"), str):
        row["payload"] = json.loads(row["payload"])
    return row
