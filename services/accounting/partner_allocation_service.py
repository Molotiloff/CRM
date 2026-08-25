from __future__ import annotations

from decimal import Decimal, InvalidOperation

from domain import Deal, DomainStateError, DomainValidationError, validate_partner_allocations
from services.crm.deal_service import DealCreateCommand
from services.unit_of_work import UnitOfWorkFactory

from .firm_position_service import FirmPositionAccountingService
from .models import (
    AllocatePartnerPurchase,
    PartnerAllocation,
    PartnerAllocationDestination,
    PartnerPurchaseContext,
    RecordPurchase,
    SettlePartnerTransfer,
)


class PartnerPurchaseAllocationService:
    """Owns partner RUB funding and allocation without duplicating settlement ledgers."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        position_service: FirmPositionAccountingService,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._position_service = position_service

    async def create_purchase(
        self,
        command: DealCreateCommand,
        *,
        partner_account_id: int,
        idempotency_key: str,
    ) -> Deal:
        key = idempotency_key.strip()
        if not key:
            raise DomainValidationError("Partner purchase idempotency key is required")
        if str(command.deal_type) != "purchase":
            raise DomainValidationError("Partner purchase must use purchase deal type")
        qty, rate, currency = self._purchase_values(command.body.to_dict())
        if currency != "USDT":
            raise DomainValidationError("Partner allocation currently supports USDT purchases")
        async with self._unit_of_work_factory() as unit_of_work:
            deal, _created = await unit_of_work.deals.create_deal_idempotent(command)
            await unit_of_work.partner_allocations.record_partner_rub_balance(
                account_id=partner_account_id,
                deal_id=deal.id,
                amount=qty * rate,
                actor_user_id=command.actor_user_id,
                idempotency_key=f"{key}:rub",
            )
            await unit_of_work.commit()
            return deal

    async def allocate(self, command: AllocatePartnerPurchase) -> PartnerAllocation:
        qty = self._positive(command.qty, "allocation quantity")
        key = command.idempotency_key.strip()
        if not key:
            raise DomainValidationError("Allocation idempotency key is required")
        async with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.partner_allocations
            await repository.acquire_purchase_lock(command.purchase_deal_id)
            repeated = await repository.get_by_idempotency_key(key)
            if repeated is not None:
                self._validate_replay(repeated, command, qty)
                return repeated
            purchase = await self._purchase(repository, command.purchase_deal_id)
            if (
                command.destination is PartnerAllocationDestination.CLIENT_DIRECT
                and command.sale_deal_id is not None
                and await repository.has_non_reversed_sale_allocation(command.sale_deal_id)
            ):
                raise DomainStateError("Sale already has a partner allocation source")
            allocated = await repository.allocated_quantity(purchase.deal_id)
            allocations = (qty,) if allocated == 0 else (allocated, qty)
            validate_partner_allocations(
                purchase_quantity=purchase.qty,
                allocations=allocations,
            )
            if command.destination is PartnerAllocationDestination.CLIENT_DIRECT:
                allocation = await self._allocate_client(repository, command, qty)
            elif command.destination is PartnerAllocationDestination.FIRM_WALLET:
                if command.sale_deal_id is not None:
                    raise DomainValidationError("Firm-wallet allocation must not reference a sale")
                allocation = await repository.create_firm_wallet_allocation(
                    purchase_deal_id=purchase.deal_id,
                    qty=qty,
                    idempotency_key=key,
                    actor_user_id=command.actor_user_id,
                )
            else:
                raise DomainValidationError("Unsupported partner allocation destination")
            await unit_of_work.commit()
            return allocation

    async def settle_firm_wallet(self, allocation_id: int) -> PartnerAllocation:
        async with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.partner_allocations
            allocation = await repository.get_for_update(allocation_id)
            if allocation is None:
                raise DomainValidationError("Partner allocation was not found")
            if allocation.destination is not PartnerAllocationDestination.FIRM_WALLET:
                raise DomainValidationError("Allocation is not directed to the firm wallet")
            if allocation.status.value == "settled":
                return allocation
            purchase = await self._purchase(repository, allocation.purchase_deal_id)
            move = await self._position_service.record_purchase(
                RecordPurchase(
                    currency=purchase.currency,
                    qty=allocation.qty,
                    rate=purchase.rate,
                    deal_id=purchase.deal_id,
                    actor_user_id=None,
                    idempotency_key=f"partner-allocation:{allocation.id}:position",
                ),
                unit_of_work=unit_of_work,
            )
            result = await repository.settle_firm_wallet(
                allocation_id=allocation.id,
                position_move_id=move.id,
            )
            await unit_of_work.commit()
            return result

    async def settle_client_transfer(
        self, command: SettlePartnerTransfer
    ) -> PartnerAllocation:
        qty = self._positive(command.actual_qty, "partner transfer quantity")
        if command.confirmed_at.tzinfo is None:
            raise DomainValidationError("Partner transfer confirmation must be timezone-aware")
        async with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.partner_allocations
            allocation = await repository.get_for_update(command.allocation_id)
            if allocation is None:
                raise DomainValidationError("Partner allocation was not found")
            if allocation.destination is not PartnerAllocationDestination.CLIENT_DIRECT:
                raise DomainValidationError("Allocation is not a direct client transfer")
            if allocation.status.value == "settled":
                return allocation
            result = await repository.settle_client_transfer(
                allocation_id=allocation.id,
                actual_qty=qty,
                tx_hash=command.tx_hash,
                event_index=command.event_index,
                confirmed_at=command.confirmed_at,
            )
            await unit_of_work.commit()
            return result

    async def _allocate_client(self, repository, command, qty: Decimal) -> PartnerAllocation:
        if command.sale_deal_id is None:
            raise DomainValidationError("Direct client allocation requires a sale")
        fields = (command.network, command.partner_address, command.destination_address)
        if any(not str(value or "").strip() for value in fields):
            raise DomainValidationError("Direct client allocation requires transfer addresses")
        if await repository.has_non_reversed_sale_allocation(command.sale_deal_id):
            raise DomainStateError("Sale already has a partner allocation source")
        sale_qty = await repository.sale_quantity(command.sale_deal_id)
        if sale_qty is None:
            raise DomainValidationError("Sale deal was not found")
        if sale_qty != qty:
            raise DomainValidationError("Direct allocation must cover the full sale quantity")
        return await repository.create_client_allocation(
            purchase_deal_id=command.purchase_deal_id,
            sale_deal_id=command.sale_deal_id,
            qty=qty,
            network=str(command.network),
            partner_address=str(command.partner_address),
            destination_address=str(command.destination_address),
            idempotency_key=command.idempotency_key.strip(),
            actor_user_id=command.actor_user_id,
        )

    @staticmethod
    async def _purchase(repository, deal_id: int) -> PartnerPurchaseContext:
        purchase = await repository.purchase_context(deal_id)
        if purchase is None:
            raise DomainValidationError("Partner purchase deal was not found or is incomplete")
        if str(purchase.currency) != "USDT":
            raise DomainValidationError("Partner allocation currently supports USDT purchases")
        return purchase

    @staticmethod
    def _purchase_values(body: dict) -> tuple[Decimal, Decimal, str]:
        try:
            qty = Decimal(str(body["qty"]))
            rate = Decimal(str(body["rate"]))
        except (InvalidOperation, KeyError, TypeError, ValueError):
            raise DomainValidationError("Partner purchase requires qty and rate") from None
        if qty <= 0 or rate <= 0:
            raise DomainValidationError("Partner purchase qty and rate must be positive")
        return qty, rate, str(body.get("currency") or "USDT").strip().upper()

    @staticmethod
    def _positive(value: object, field: str) -> Decimal:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise DomainValidationError(f"Invalid {field}") from None
        if not parsed.is_finite() or parsed <= 0:
            raise DomainValidationError(f"{field.capitalize()} must be positive")
        return parsed

    @staticmethod
    def _validate_replay(
        allocation: PartnerAllocation,
        command: AllocatePartnerPurchase,
        qty: Decimal,
    ) -> None:
        if (
            allocation.purchase_deal_id != command.purchase_deal_id
            or allocation.sale_deal_id != command.sale_deal_id
            or allocation.destination is not command.destination
            or allocation.qty != qty
        ):
            raise DomainStateError("Idempotency key belongs to another partner allocation")
