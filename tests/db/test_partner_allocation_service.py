from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from db_asyncpg.uow import AsyncpgUnitOfWork
from domain import (
    DealSource,
    DealType,
    DomainStateError,
    DomainValidationError,
    SourceKind,
)
from services.accounting import FirmPositionAccountingService
from services.accounting.models import (
    AllocatePartnerPurchase,
    PartnerAllocationDestination,
    PartnerAllocationStatus,
    SettlePartnerTransfer,
)
from services.accounting.partner_allocation_service import (
    PartnerPurchaseAllocationService,
)
from services.crm.deal_service import DealCreateCommand


def _service(pool) -> PartnerPurchaseAllocationService:
    def factory():
        return AsyncpgUnitOfWork(pool)

    return PartnerPurchaseAllocationService(
        factory,
        position_service=FirmPositionAccountingService(factory),
    )


async def _purchase(service, account_id: int, ref: str = "partner-purchase:1"):
    return await service.create_purchase(
        DealCreateCommand(
            deal_type=DealType.PURCHASE,
            city="Екб",
            actor_user_id=None,
            source=DealSource.CRM,
            source_kind=SourceKind.PARTNER,
            source_ref=ref,
            body={"currency": "USDT", "qty": "100", "rate": "90"},
        ),
        partner_account_id=account_id,
        idempotency_key=ref,
    )


@pytest.mark.asyncio
async def test_partner_purchase_rub_leg_is_recorded_once(pool) -> None:
    async with pool.acquire() as connection:
        account_id = await connection.fetchval(
            "INSERT INTO internal_accounts(name, kind) VALUES ('Partner A', 'partner') RETURNING id"
        )
    service = _service(pool)

    first = await _purchase(service, int(account_id))
    repeated = await _purchase(service, int(account_id))

    assert repeated.id == first.id
    async with pool.acquire() as connection:
        account = await connection.fetchrow(
            "SELECT balance FROM internal_accounts WHERE id = $1", account_id
        )
        moves = await connection.fetchval(
            "SELECT COUNT(*) FROM internal_account_moves WHERE deal_id = $1", first.id
        )
    assert account["balance"] == Decimal("9000.00000000")
    assert moves == 1


@pytest.mark.asyncio
async def test_partner_allocations_limit_and_destination_accounting(pool) -> None:
    async with pool.acquire() as connection:
        account_id = await connection.fetchval(
            "INSERT INTO internal_accounts(name, kind) VALUES ('Partner B', 'partner') RETURNING id"
        )
        sale_id = await connection.fetchval(
            """
            INSERT INTO deals(deal_type, status, source, body)
            VALUES ('sale', 'new', 'crm', '{"currency":"USDT","qty":"60"}')
            RETURNING id
            """
        )
    service = _service(pool)
    purchase = await _purchase(service, int(account_id), "partner-purchase:2")
    direct = await service.allocate(
        AllocatePartnerPurchase(
            purchase_deal_id=purchase.id,
            sale_deal_id=int(sale_id),
            destination=PartnerAllocationDestination.CLIENT_DIRECT,
            qty=Decimal("60"),
            network="TRON",
            partner_address="partner",
            destination_address="client",
            idempotency_key="allocation:direct",
        )
    )
    wallet = await service.allocate(
        AllocatePartnerPurchase(
            purchase_deal_id=purchase.id,
            destination=PartnerAllocationDestination.FIRM_WALLET,
            qty=Decimal("40"),
            idempotency_key="allocation:wallet",
        )
    )

    with pytest.raises(DomainValidationError):
        await service.allocate(
            AllocatePartnerPurchase(
                purchase_deal_id=purchase.id,
                destination=PartnerAllocationDestination.FIRM_WALLET,
                qty=Decimal("1"),
                idempotency_key="allocation:over-limit",
            )
        )

    direct = await service.settle_client_transfer(
        SettlePartnerTransfer(
            allocation_id=direct.id,
            actual_qty=Decimal("60"),
            tx_hash="ABC123",
            event_index=0,
            confirmed_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
    )
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT COUNT(*) FROM firm_position_moves") == 0

    wallet = await service.settle_firm_wallet(wallet.id)
    assert direct.status is PartnerAllocationStatus.SETTLED
    assert wallet.status is PartnerAllocationStatus.SETTLED
    async with pool.acquire() as connection:
        position = await connection.fetchrow(
            "SELECT qty_after, entry_rate FROM firm_position_moves"
        )
        rub_moves = await connection.fetchval(
            "SELECT COUNT(*) FROM internal_account_moves WHERE deal_id = $1", purchase.id
        )
    assert (position["qty_after"], position["entry_rate"]) == (
        Decimal("40.00000000"),
        Decimal("90.00000000"),
    )
    assert rub_moves == 1

    with pytest.raises(DomainStateError):
        await service.allocate(
            AllocatePartnerPurchase(
                purchase_deal_id=purchase.id,
                sale_deal_id=int(sale_id),
                destination=PartnerAllocationDestination.CLIENT_DIRECT,
                qty=Decimal("60"),
                network="TRON",
                partner_address="partner-2",
                destination_address="client",
                idempotency_key="allocation:duplicate-sale",
            )
        )
