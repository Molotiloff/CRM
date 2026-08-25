from __future__ import annotations

from decimal import Decimal

from db_asyncpg.repositories.base import ConnectionBoundRepo
from domain import DomainStateError, DomainValidationError
from services.accounting.fulfillment_models import (
    ClientWithdrawalContext,
    EnqueueFulfillment,
    FulfillmentQueueItem,
    FulfillmentQueueSummary,
    FulfillmentRequestKind,
    FulfillmentStatus,
    ReorderFulfillment,
)

_ITEM_COLUMNS = """
    id, deal_id, request_kind, qty, sequence_no, status, created_by,
    created_at, reordered_by, reordered_at, payment_watch_id,
    payment_event_id, position_move_id, wallet_source
"""


class FulfillmentQueueRepo(ConnectionBoundRepo):
    _SEQUENCE_STEP = 1024

    async def acquire_client_lock(self, chat_id: int) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                f"usdt_fulfillment_client:{chat_id}",
            )

    async def client_withdrawal_context(
        self,
        *,
        chat_id: int,
    ) -> ClientWithdrawalContext | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                SELECT c.id AS client_id,
                       COALESCE((
                           SELECT t.balance_after
                           FROM client_accounts a
                           JOIN transactions t ON t.account_id = a.id
                           WHERE a.client_id = c.id AND a.currency_code = 'USDT'
                           ORDER BY t.id DESC
                           LIMIT 1
                       ), 0) AS qty
                FROM clients c
                WHERE c.chat_id = $1
                """,
                chat_id,
            )
        if row is None:
            return None
        return ClientWithdrawalContext(
            client_id=int(row["client_id"]),
            qty=Decimal(str(row["qty"])),
        )

    async def enqueue(self, command: EnqueueFulfillment) -> FulfillmentQueueItem:
        if command.qty <= 0:
            raise DomainValidationError("Fulfillment quantity must be greater than zero")
        async with self._connection() as connection:
            await self._acquire_order_lock(connection)
            existing = await connection.fetchrow(
                f"""
                SELECT {_ITEM_COLUMNS}
                FROM usdt_fulfillment_queue
                WHERE deal_id = $1 AND status IN ('queued', 'executing')
                """,
                command.deal_id,
            )
            if existing is not None:
                item = self._item(existing)
                if item.request_kind is not command.request_kind or item.qty != command.qty:
                    raise DomainStateError("Deal already has another active fulfillment")
                return item
            next_sequence = int(
                await connection.fetchval(
                    """
                    SELECT COALESCE(MAX(sequence_no), 0) + $1
                    FROM usdt_fulfillment_queue
                    """,
                    self._SEQUENCE_STEP,
                )
            )
            row = await connection.fetchrow(
                f"""
                INSERT INTO usdt_fulfillment_queue(
                    deal_id, request_kind, qty, sequence_no, created_by
                )
                VALUES ($1, $2, $3, $4, $5)
                RETURNING {_ITEM_COLUMNS}
                """,
                command.deal_id,
                str(command.request_kind),
                command.qty,
                next_sequence,
                command.actor_user_id,
            )
        if row is None:
            raise RuntimeError("Fulfillment enqueue returned no row")
        return self._item(row)

    async def list_active(self) -> list[FulfillmentQueueItem]:
        async with self._connection() as connection:
            rows = await connection.fetch(
                f"""
                SELECT {_ITEM_COLUMNS}
                FROM usdt_fulfillment_queue
                WHERE status IN ('queued', 'executing')
                ORDER BY sequence_no, id
                """
            )
        return [self._item(row) for row in rows]

    async def reorder(self, command: ReorderFulfillment) -> FulfillmentQueueItem:
        async with self._connection() as connection:
            await self._acquire_order_lock(connection)
            rows = await connection.fetch(
                """
                SELECT id
                FROM usdt_fulfillment_queue
                WHERE status IN ('queued', 'executing')
                ORDER BY sequence_no, id
                FOR UPDATE
                """
            )
            item_ids = [int(row["id"]) for row in rows]
            if command.item_id not in item_ids:
                raise DomainValidationError("Active fulfillment item was not found")
            if (
                command.before_item_id is not None
                and command.before_item_id not in item_ids
            ):
                raise DomainValidationError("Target fulfillment item was not found")
            if command.item_id == command.before_item_id:
                return await self._require_item(connection, command.item_id)

            item_ids.remove(command.item_id)
            target_index = (
                item_ids.index(command.before_item_id)
                if command.before_item_id is not None
                else len(item_ids)
            )
            item_ids.insert(target_index, command.item_id)
            sequence_base = int(
                await connection.fetchval(
                    "SELECT COALESCE(MAX(sequence_no), 0) FROM usdt_fulfillment_queue"
                )
            )
            for index, item_id in enumerate(item_ids, start=1):
                await connection.execute(
                    """
                    UPDATE usdt_fulfillment_queue
                    SET sequence_no = $2,
                        reordered_by = CASE WHEN id = $3 THEN $4 ELSE reordered_by END,
                        reordered_at = CASE WHEN id = $3 THEN NOW() ELSE reordered_at END
                    WHERE id = $1
                    """,
                    item_id,
                    sequence_base + index * self._SEQUENCE_STEP,
                    command.item_id,
                    command.actor_user_id,
                )
            return await self._require_item(connection, command.item_id)

    async def summary(self) -> FulfillmentQueueSummary:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                SELECT COALESCE((
                           SELECT SUM(qty)
                           FROM usdt_fulfillment_queue
                           WHERE status IN ('queued', 'executing')
                       ), 0) AS queued_qty,
                       (
                           SELECT actual_qty
                           FROM firm_wallet_fact_snapshots
                           WHERE currency_code = 'USDT'
                           ORDER BY observed_at DESC, id DESC
                           LIMIT 1
                       ) AS usdt_fact
                """
            )
        queued = Decimal(str(row["queued_qty"]))
        raw_fact = row["usdt_fact"]
        if raw_fact is None:
            return FulfillmentQueueSummary(
                queued_qty=queued,
                usdt_fact=None,
                queue_shortage_qty=None,
                onchain_liquid_qty=None,
            )
        fact = Decimal(str(raw_fact))
        return FulfillmentQueueSummary(
            queued_qty=queued,
            usdt_fact=fact,
            queue_shortage_qty=max(queued - fact, Decimal(0)),
            onchain_liquid_qty=max(fact - queued, Decimal(0)),
        )

    async def cancel(self, *, item_id: int, reason: str) -> FulfillmentQueueItem:
        if not reason.strip():
            raise DomainValidationError("Fulfillment cancellation reason is required")
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"""
                UPDATE usdt_fulfillment_queue
                SET status = 'canceled', cancel_reason = $2
                WHERE id = $1 AND status = 'queued'
                RETURNING {_ITEM_COLUMNS}
                """,
                item_id,
                reason.strip(),
            )
        if row is None:
            raise DomainStateError("Only a queued fulfillment can be canceled")
        return self._item(row)

    async def next_for_client_for_update(
        self,
        *,
        chat_id: int,
    ) -> FulfillmentQueueItem | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"""
                SELECT {_qualified_columns('q')}
                FROM usdt_fulfillment_queue q
                JOIN deals d ON d.id = q.deal_id
                JOIN clients c ON c.id = d.client_id
                WHERE c.chat_id = $1 AND q.status = 'queued'
                ORDER BY q.sequence_no, q.id
                FOR UPDATE OF q SKIP LOCKED
                LIMIT 1
                """,
                chat_id,
            )
        return self._item(row) if row is not None else None

    async def executing_qty(self) -> Decimal:
        async with self._connection() as connection:
            value = await connection.fetchval(
                """
                SELECT COALESCE(SUM(qty), 0)
                FROM usdt_fulfillment_queue
                WHERE status = 'executing'
                """
            )
        return Decimal(str(value))

    async def mark_executing(
        self,
        *,
        item_id: int,
        watch_id: int,
    ) -> FulfillmentQueueItem:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"""
                UPDATE usdt_fulfillment_queue
                SET status = 'executing', payment_watch_id = $2
                WHERE id = $1 AND status = 'queued'
                RETURNING {_ITEM_COLUMNS}
                """,
                item_id,
                watch_id,
            )
        if row is None:
            raise DomainStateError("Fulfillment is no longer queued")
        return self._item(row)

    async def get_by_deal_for_update(
        self,
        *,
        deal_id: int,
    ) -> FulfillmentQueueItem | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"""
                SELECT {_ITEM_COLUMNS}
                FROM usdt_fulfillment_queue
                WHERE deal_id = $1 AND status IN ('queued', 'executing', 'completed')
                ORDER BY id DESC
                FOR UPDATE
                LIMIT 1
                """,
                deal_id,
            )
        return self._item(row) if row is not None else None

    async def complete(
        self,
        *,
        item_id: int,
        payment_event_id: int,
        position_move_id: int | None,
        actor_user_id: int | None,
        wallet_source: str,
    ) -> FulfillmentQueueItem:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"""
                UPDATE usdt_fulfillment_queue
                SET status = 'completed', completed_at = NOW(),
                    payment_event_id = $2, position_move_id = $3,
                    completed_by = $4, wallet_source = $5
                WHERE id = $1 AND status = 'executing'
                RETURNING {_ITEM_COLUMNS}
                """,
                item_id,
                payment_event_id,
                position_move_id,
                actor_user_id,
                wallet_source,
            )
        if row is None:
            repeated = await self.get_by_deal_for_update(
                deal_id=(await self._require_item_by_id(item_id)).deal_id
            )
            if (
                repeated is not None
                and repeated.status is FulfillmentStatus.COMPLETED
                and repeated.payment_event_id == payment_event_id
            ):
                return repeated
            raise DomainStateError("Fulfillment is not executing")
        return self._item(row)

    @staticmethod
    async def _acquire_order_lock(connection) -> None:
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('usdt_fulfillment_queue', 0))"
        )

    async def _require_item(self, connection, item_id: int) -> FulfillmentQueueItem:
        row = await connection.fetchrow(
            f"SELECT {_ITEM_COLUMNS} FROM usdt_fulfillment_queue WHERE id = $1",
            item_id,
        )
        if row is None:
            raise DomainValidationError("Fulfillment item was not found")
        return self._item(row)

    async def _require_item_by_id(self, item_id: int) -> FulfillmentQueueItem:
        async with self._connection() as connection:
            return await self._require_item(connection, item_id)

    @staticmethod
    def _item(row) -> FulfillmentQueueItem:
        return FulfillmentQueueItem(
            id=int(row["id"]),
            deal_id=int(row["deal_id"]),
            request_kind=FulfillmentRequestKind(str(row["request_kind"])),
            qty=Decimal(str(row["qty"])),
            sequence_no=int(row["sequence_no"]),
            status=FulfillmentStatus(str(row["status"])),
            created_by=int(row["created_by"]) if row["created_by"] is not None else None,
            created_at=row["created_at"],
            reordered_by=(
                int(row["reordered_by"]) if row["reordered_by"] is not None else None
            ),
            reordered_at=row["reordered_at"],
            payment_watch_id=(
                int(row["payment_watch_id"])
                if row["payment_watch_id"] is not None
                else None
            ),
            payment_event_id=(
                int(row["payment_event_id"])
                if row["payment_event_id"] is not None
                else None
            ),
            position_move_id=(
                int(row["position_move_id"])
                if row["position_move_id"] is not None
                else None
            ),
            wallet_source=(
                str(row["wallet_source"]) if row["wallet_source"] is not None else None
            ),
        )


def _qualified_columns(alias: str) -> str:
    return ", ".join(
        f"{alias}.{column.strip()}"
        for column in _ITEM_COLUMNS.split(",")
        if column.strip()
    )
