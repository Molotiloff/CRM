from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import asyncpg

from domain import CurrencyCode, DomainStateError
from services.accounting.models import (
    PartnerAllocation,
    PartnerAllocationDestination,
    PartnerAllocationStatus,
    PartnerPurchaseContext,
)

from .base import ConnectionBoundRepo

_ALLOCATION_COLUMNS = """
    id, purchase_deal_id, sale_deal_id, destination_kind, qty, status,
    transfer_watch_id, position_move_id, idempotency_key
"""


class PartnerAllocationsRepo(ConnectionBoundRepo):
    async def acquire_purchase_lock(self, purchase_deal_id: int) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('partner_purchase:' || $1, 0))",
                str(purchase_deal_id),
            )

    async def purchase_context(self, purchase_deal_id: int) -> PartnerPurchaseContext | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                SELECT id, body->>'qty' AS qty, body->>'rate' AS rate,
                       UPPER(BTRIM(body->>'currency')) AS currency
                FROM deals
                WHERE id = $1 AND deal_type = 'purchase' AND status <> 'canceled'
                FOR UPDATE
                """,
                purchase_deal_id,
            )
        if row is None or row["qty"] is None or row["rate"] is None:
            return None
        return PartnerPurchaseContext(
            deal_id=int(row["id"]),
            qty=Decimal(str(row["qty"])),
            rate=Decimal(str(row["rate"])),
            currency=CurrencyCode(str(row["currency"] or "USDT")),
        )

    async def sale_quantity(self, sale_deal_id: int) -> Decimal | None:
        async with self._connection() as connection:
            value = await connection.fetchval(
                """
                SELECT body->>'qty' FROM deals
                WHERE id = $1 AND deal_type = 'sale' AND status <> 'canceled'
                """,
                sale_deal_id,
            )
        return Decimal(str(value)) if value is not None else None

    async def allocated_quantity(self, purchase_deal_id: int) -> Decimal:
        async with self._connection() as connection:
            value = await connection.fetchval(
                """
                SELECT COALESCE(SUM(qty), 0)
                FROM partner_purchase_allocations
                WHERE purchase_deal_id = $1 AND status <> 'reversed'
                """,
                purchase_deal_id,
            )
        return Decimal(str(value))

    async def get_by_idempotency_key(self, key: str) -> PartnerAllocation | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_ALLOCATION_COLUMNS} FROM partner_purchase_allocations "
                "WHERE idempotency_key = $1",
                key,
            )
        return self._allocation(row) if row is not None else None

    async def get_for_update(self, allocation_id: int) -> PartnerAllocation | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_ALLOCATION_COLUMNS} FROM partner_purchase_allocations "
                "WHERE id = $1 FOR UPDATE",
                allocation_id,
            )
        return self._allocation(row) if row is not None else None

    async def has_non_reversed_sale_allocation(self, sale_deal_id: int) -> bool:
        async with self._connection() as connection:
            return bool(
                await connection.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM partner_purchase_allocations
                        WHERE sale_deal_id = $1 AND status <> 'reversed'
                    )
                    """,
                    sale_deal_id,
                )
            )

    async def create_firm_wallet_allocation(
        self,
        *,
        purchase_deal_id: int,
        qty: Decimal,
        idempotency_key: str,
        actor_user_id: int | None,
    ) -> PartnerAllocation:
        return await self._insert_allocation(
            purchase_deal_id=purchase_deal_id,
            sale_deal_id=None,
            destination="firm_wallet",
            qty=qty,
            transfer_watch_id=None,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
        )

    async def create_client_allocation(
        self,
        *,
        purchase_deal_id: int,
        sale_deal_id: int,
        qty: Decimal,
        network: str,
        partner_address: str,
        destination_address: str,
        idempotency_key: str,
        actor_user_id: int | None,
    ) -> PartnerAllocation:
        async with self._connection() as connection:
            watch_id = await connection.fetchval(
                """
                INSERT INTO partner_transfer_watches(
                    purchase_deal_id, sale_deal_id, network, from_address,
                    to_address, expected_qty, created_by, idempotency_key
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                purchase_deal_id,
                sale_deal_id,
                network.strip().upper(),
                partner_address.strip(),
                destination_address.strip(),
                qty,
                actor_user_id,
                f"{idempotency_key}:watch",
            )
        return await self._insert_allocation(
            purchase_deal_id=purchase_deal_id,
            sale_deal_id=sale_deal_id,
            destination="client_direct",
            qty=qty,
            transfer_watch_id=int(watch_id),
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
        )

    async def _insert_allocation(
        self,
        *,
        purchase_deal_id: int,
        sale_deal_id: int | None,
        destination: str,
        qty: Decimal,
        transfer_watch_id: int | None,
        idempotency_key: str,
        actor_user_id: int | None,
    ) -> PartnerAllocation:
        async with self._connection() as connection:
            try:
                row = await connection.fetchrow(
                    f"""
                    INSERT INTO partner_purchase_allocations(
                        purchase_deal_id, sale_deal_id, destination_kind, qty,
                        transfer_watch_id, idempotency_key, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING {_ALLOCATION_COLUMNS}
                    """,
                    purchase_deal_id,
                    sale_deal_id,
                    destination,
                    qty,
                    transfer_watch_id,
                    idempotency_key,
                    actor_user_id,
                )
            except asyncpg.UniqueViolationError as exc:
                raise DomainStateError("Partner allocation has already been applied") from exc
        return self._allocation(row)

    async def settle_firm_wallet(
        self, *, allocation_id: int, position_move_id: int
    ) -> PartnerAllocation:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                f"""
                UPDATE partner_purchase_allocations
                SET status = 'settled', settled_at = NOW(), position_move_id = $2
                WHERE id = $1 AND status = 'active'
                RETURNING {_ALLOCATION_COLUMNS}
                """,
                allocation_id,
                position_move_id,
            )
        if row is None:
            raise DomainStateError("Partner allocation is not active")
        return self._allocation(row)

    async def settle_client_transfer(
        self,
        *,
        allocation_id: int,
        actual_qty: Decimal,
        tx_hash: str,
        event_index: int,
        confirmed_at: datetime,
    ) -> PartnerAllocation:
        async with self._connection() as connection:
            allocation = await connection.fetchrow(
                """
                SELECT transfer_watch_id, qty FROM partner_purchase_allocations
                WHERE id = $1 AND status = 'active' FOR UPDATE
                """,
                allocation_id,
            )
            if allocation is None:
                raise DomainStateError("Partner allocation is not active")
            status = "matched" if Decimal(str(allocation["qty"])) == actual_qty else "needs_review"
            try:
                updated = await connection.execute(
                    """
                    UPDATE partner_transfer_watches
                    SET actual_qty = $2, status = $3, tx_hash = $4,
                        event_index = $5, confirmed_at = $6
                    WHERE id = $1 AND status = 'watching'
                    """,
                    int(allocation["transfer_watch_id"]),
                    actual_qty,
                    status,
                    tx_hash.strip().lower(),
                    event_index,
                    confirmed_at,
                )
            except asyncpg.UniqueViolationError as exc:
                raise DomainStateError("Blockchain event is already claimed") from exc
            if updated.endswith(" 0"):
                raise DomainStateError("Partner transfer watch is no longer active")
            if status != "matched":
                row = await connection.fetchrow(
                    f"SELECT {_ALLOCATION_COLUMNS} FROM partner_purchase_allocations WHERE id = $1",
                    allocation_id,
                )
                return self._allocation(row)
            row = await connection.fetchrow(
                f"""
                UPDATE partner_purchase_allocations
                SET status = 'settled', settled_at = $2
                WHERE id = $1 AND status = 'active'
                RETURNING {_ALLOCATION_COLUMNS}
                """,
                allocation_id,
                confirmed_at,
            )
        return self._allocation(row)

    async def record_partner_rub_balance(
        self,
        *,
        account_id: int,
        deal_id: int,
        amount: Decimal,
        actor_user_id: int | None,
        idempotency_key: str,
    ) -> int:
        async with self._connection() as connection:
            account = await connection.fetchrow(
                "SELECT balance, kind FROM internal_accounts WHERE id = $1 FOR UPDATE",
                account_id,
            )
            if account is None or str(account["kind"]) != "partner":
                raise DomainStateError("Partner internal account was not found")
            repeated = await connection.fetchrow(
                """
                SELECT id, account_id, deal_id, amount
                FROM internal_account_moves WHERE idempotency_key = $1
                """,
                idempotency_key,
            )
            if repeated is not None:
                if (
                    int(repeated["account_id"]) != account_id
                    or int(repeated["deal_id"]) != deal_id
                    or Decimal(str(repeated["amount"])) != amount
                ):
                    raise DomainStateError(
                        "Idempotency key belongs to another partner RUB movement"
                    )
                return int(repeated["id"])
            balance_after = Decimal(str(account["balance"])) + amount
            move_id = await connection.fetchval(
                """
                INSERT INTO internal_account_moves(
                    account_id, amount, balance_after, deal_id, actor_user_id,
                    comment, idempotency_key
                ) VALUES ($1, $2, $3, $4, $5, 'Partner purchase RUB obligation', $6)
                RETURNING id
                """,
                account_id,
                amount,
                balance_after,
                deal_id,
                actor_user_id,
                idempotency_key,
            )
            await connection.execute(
                "UPDATE internal_accounts SET balance = $2 WHERE id = $1",
                account_id,
                balance_after,
            )
        return int(move_id)

    @staticmethod
    def _allocation(row) -> PartnerAllocation:
        return PartnerAllocation(
            id=int(row["id"]),
            purchase_deal_id=int(row["purchase_deal_id"]),
            sale_deal_id=int(row["sale_deal_id"]) if row["sale_deal_id"] is not None else None,
            destination=PartnerAllocationDestination(str(row["destination_kind"])),
            qty=Decimal(str(row["qty"])),
            status=PartnerAllocationStatus(str(row["status"])),
            transfer_watch_id=(
                int(row["transfer_watch_id"]) if row["transfer_watch_id"] is not None else None
            ),
            position_move_id=(
                int(row["position_move_id"]) if row["position_move_id"] is not None else None
            ),
            idempotency_key=str(row["idempotency_key"]),
        )
