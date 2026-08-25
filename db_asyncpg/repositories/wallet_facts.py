from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from domain import CurrencyCode
from services.accounting.models import (
    FirmWalletAddress,
    WalletFactSnapshot,
    WalletFactSource,
)

from .base import ConnectionBoundRepo


class WalletFactsRepo(ConnectionBoundRepo):
    async def acquire_network_lock(self, network: str) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"firm_wallet_address:{network.strip().upper()}",
            )

    async def acquire_fact_lock(self, currency: CurrencyCode) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"firm_wallet_fact:{currency}",
            )

    async def get_active_address(self, network: str) -> FirmWalletAddress | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                SELECT id, network, address, active_from, active_to, reason, created_by
                FROM firm_wallet_addresses
                WHERE UPPER(BTRIM(network)) = $1 AND active_to IS NULL
                """,
                network.strip().upper(),
            )
        return self._address_from_row(row) if row is not None else None

    async def insert_address(
        self,
        *,
        network: str,
        address: str,
        active_from: datetime,
        reason: str,
        created_by: int | None,
    ) -> FirmWalletAddress:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO firm_wallet_addresses(
                    network, address, active_from, reason, created_by
                )
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, network, address, active_from, active_to, reason, created_by
                """,
                network.strip().upper(),
                address.strip(),
                active_from,
                reason.strip(),
                created_by,
            )
        if row is None:  # pragma: no cover - INSERT RETURNING always returns a row
            raise RuntimeError("Could not create firm wallet address")
        return self._address_from_row(row)

    async def close_address(self, address_id: int, *, active_to: datetime) -> None:
        async with self._connection() as connection:
            result = await connection.execute(
                """
                UPDATE firm_wallet_addresses
                SET active_to = $2
                WHERE id = $1 AND active_to IS NULL
                """,
                address_id,
                active_to,
            )
        if result != "UPDATE 1":
            raise RuntimeError("Active firm wallet address was not found")

    async def latest_snapshot(
        self,
        currency: CurrencyCode,
        *,
        address_id: int | None = None,
    ) -> WalletFactSnapshot | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                SELECT id, currency_code, actual_qty, observed_at, source, address_id,
                       actor_user_id, comment, idempotency_key
                FROM firm_wallet_fact_snapshots
                WHERE currency_code = $1
                  AND ($2::bigint IS NULL OR address_id = $2)
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
                """,
                str(currency),
                address_id,
            )
        return self._snapshot_from_row(row) if row is not None else None

    async def find_snapshot_by_idempotency_key(
        self,
        key: str,
    ) -> WalletFactSnapshot | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                SELECT id, currency_code, actual_qty, observed_at, source, address_id,
                       actor_user_id, comment, idempotency_key
                FROM firm_wallet_fact_snapshots
                WHERE idempotency_key = $1
                """,
                key,
            )
        return self._snapshot_from_row(row) if row is not None else None

    async def append_snapshot(
        self,
        *,
        currency: CurrencyCode,
        actual_qty: Decimal,
        observed_at: datetime,
        source: WalletFactSource,
        address_id: int | None,
        actor_user_id: int | None,
        comment: str | None,
        idempotency_key: str,
    ) -> WalletFactSnapshot:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO firm_wallet_fact_snapshots(
                    currency_code, address_id, actual_qty, observed_at, source,
                    actor_user_id, comment, idempotency_key
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id, currency_code, actual_qty, observed_at, source, address_id,
                          actor_user_id, comment, idempotency_key
                """,
                str(currency),
                address_id,
                actual_qty,
                observed_at,
                str(source),
                actor_user_id,
                comment,
                idempotency_key,
            )
        if row is None:  # pragma: no cover - INSERT RETURNING always returns a row
            raise RuntimeError("Could not append firm wallet fact snapshot")
        return self._snapshot_from_row(row)

    @staticmethod
    def _address_from_row(row) -> FirmWalletAddress:
        return FirmWalletAddress(
            id=int(row["id"]),
            network=str(row["network"]),
            address=str(row["address"]),
            active_from=row["active_from"],
            active_to=row["active_to"],
            reason=str(row["reason"]),
            created_by=int(row["created_by"]) if row["created_by"] is not None else None,
        )

    @staticmethod
    def _snapshot_from_row(row) -> WalletFactSnapshot:
        return WalletFactSnapshot(
            id=int(row["id"]),
            currency=CurrencyCode(str(row["currency_code"])),
            actual_qty=Decimal(str(row["actual_qty"])),
            observed_at=row["observed_at"],
            source=WalletFactSource(str(row["source"])),
            address_id=int(row["address_id"]) if row["address_id"] is not None else None,
            actor_user_id=(
                int(row["actor_user_id"]) if row["actor_user_id"] is not None else None
            ),
            comment=str(row["comment"]) if row["comment"] is not None else None,
            idempotency_key=(
                str(row["idempotency_key"])
                if row["idempotency_key"] is not None
                else None
            ),
        )
