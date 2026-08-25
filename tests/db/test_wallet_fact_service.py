from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from db_asyncpg.uow import AsyncpgUnitOfWork
from domain import DomainStateError
from services.accounting.models import (
    RecordWalletFact,
    RotateFirmWalletAddress,
    WalletFactSource,
)
from services.accounting.wallet_fact_service import WalletFactService

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _service(pool) -> WalletFactService:
    return WalletFactService(lambda: AsyncpgUnitOfWork(pool))


@pytest.mark.asyncio
async def test_wallet_rotation_requires_confirmed_zero_balance(pool) -> None:
    service = _service(pool)
    started_at = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
    first = await service.rotate_address(
        RotateFirmWalletAddress(
            network="tron",
            address="first-address",
            active_from=started_at,
            reason="Initial controlled address",
        )
    )
    await service.record(
        RecordWalletFact(
            currency="USDT",
            actual_qty=Decimal("12.5"),
            observed_at=started_at + timedelta(minutes=1),
            source=WalletFactSource.TRONSCAN,
            network="TRON",
            idempotency_key="fact:non-zero",
        )
    )

    with pytest.raises(DomainStateError, match="confirmed zero balance"):
        await service.rotate_address(
            RotateFirmWalletAddress(
                network="TRON",
                address="second-address",
                active_from=started_at + timedelta(hours=1),
                reason="TRX resource rotation",
            )
        )

    await service.record(
        RecordWalletFact(
            currency="USDT",
            actual_qty=Decimal(0),
            observed_at=started_at + timedelta(minutes=30),
            source=WalletFactSource.TRONSCAN,
            network="TRON",
            idempotency_key="fact:zero",
        )
    )
    second = await service.rotate_address(
        RotateFirmWalletAddress(
            network="TRON",
            address="second-address",
            active_from=started_at + timedelta(hours=1),
            reason="TRX resource rotation",
        )
    )

    assert second.id != first.id
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT address, active_to FROM firm_wallet_addresses ORDER BY id"
        )
    assert rows[0]["active_to"] == started_at + timedelta(hours=1)
    assert rows[1]["active_to"] is None


@pytest.mark.asyncio
async def test_wallet_fact_is_idempotent_and_latest_uses_observation_time(pool) -> None:
    service = _service(pool)
    observed_at = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
    command = RecordWalletFact(
        currency="EUR",
        actual_qty=Decimal("100.25"),
        observed_at=observed_at,
        source=WalletFactSource.CASH_CHAT_LEDGER,
        idempotency_key="cash:eur:1",
    )

    first = await service.record(command)
    repeated = await service.record(command)
    await service.record(
        RecordWalletFact(
            currency="EUR",
            actual_qty=Decimal("90"),
            observed_at=observed_at - timedelta(hours=1),
            source=WalletFactSource.IMPORT,
            idempotency_key="cash:eur:older",
        )
    )

    assert repeated.id == first.id
    async with AsyncpgUnitOfWork(pool) as unit_of_work:
        latest = await unit_of_work.wallet_facts.latest_snapshot(first.currency)
    assert latest is not None
    assert latest.id == first.id
    assert latest.actual_qty == Decimal("100.25000000")


@pytest.mark.asyncio
async def test_legacy_wallet_fact_backfill_is_idempotent(pool) -> None:
    updated_at = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO firm_wallet_facts(currency_code, actual_qty, comment, updated_at)
            VALUES ('USDT', 42.5, 'legacy observation', $1)
            """,
            updated_at,
        )
        sql = (
            PROJECT_ROOT / "db_asyncpg/migrations/sql/0011_wallet_fact_backfill.sql"
        ).read_text(encoding="utf-8")
        await connection.execute(sql)
        await connection.execute(sql)
        row = await connection.fetchrow(
            """
            SELECT actual_qty, observed_at, source, address_id, idempotency_key
            FROM firm_wallet_fact_snapshots
            WHERE idempotency_key = 'legacy-firm-wallet-fact:USDT'
            """
        )
        count = await connection.fetchval(
            """
            SELECT COUNT(*) FROM firm_wallet_fact_snapshots
            WHERE idempotency_key = 'legacy-firm-wallet-fact:USDT'
            """
        )

    assert row is not None
    assert row["actual_qty"] == Decimal("42.50000000")
    assert row["observed_at"] == updated_at
    assert row["source"] == "import"
    assert row["address_id"] is None
    assert count == 1
