from __future__ import annotations

from decimal import Decimal

from domain import DomainStateError, DomainValidationError, firm_position_currency
from domain.accounting import accounting_decimal
from services.unit_of_work import UnitOfWorkFactory

from .models import (
    FirmWalletAddress,
    RecordWalletFact,
    RotateFirmWalletAddress,
    WalletFactSnapshot,
)


class WalletFactService:
    """Append-only physical facts and audited rotation of firm wallet addresses."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def record(self, command: RecordWalletFact) -> WalletFactSnapshot:
        currency = firm_position_currency(command.currency)
        quantity = accounting_decimal(command.actual_qty, field="wallet actual quantity")
        if quantity < 0:
            raise DomainValidationError("Wallet actual quantity must not be negative")
        if not command.idempotency_key.strip():
            raise DomainValidationError("Wallet fact idempotency key is required")

        async with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.wallet_facts
            await repository.acquire_fact_lock(currency)
            repeated = await repository.find_snapshot_by_idempotency_key(
                command.idempotency_key
            )
            if repeated is not None:
                if (
                    repeated.currency != currency
                    or repeated.actual_qty != quantity
                    or repeated.observed_at != command.observed_at
                    or repeated.source is not command.source
                ):
                    raise DomainStateError("Wallet fact idempotency key belongs to other data")
                return repeated

            address_id = None
            if command.network is not None:
                active_address = await repository.get_active_address(command.network)
                if active_address is None:
                    raise DomainStateError("Active firm wallet address was not found")
                address_id = active_address.id
            elif str(currency) == "USDT" and command.source.value != "import":
                raise DomainValidationError("USDT wallet fact requires an active network")

            snapshot = await repository.append_snapshot(
                currency=currency,
                actual_qty=quantity,
                observed_at=command.observed_at,
                source=command.source,
                address_id=address_id,
                actor_user_id=command.actor_user_id,
                comment=command.comment,
                idempotency_key=command.idempotency_key,
            )
            await unit_of_work.commit()
            return snapshot

    async def rotate_address(
        self,
        command: RotateFirmWalletAddress,
    ) -> FirmWalletAddress:
        network = command.network.strip().upper()
        address = command.address.strip()
        reason = command.reason.strip()
        if not network or not address or not reason:
            raise DomainValidationError("Network, address and rotation reason are required")

        async with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.wallet_facts
            await repository.acquire_network_lock(network)
            current = await repository.get_active_address(network)
            if current is not None and current.address == address:
                return current
            if current is not None:
                latest = await repository.latest_snapshot(
                    firm_position_currency("USDT"),
                    address_id=current.id,
                )
                if latest is None or latest.actual_qty != Decimal(0):
                    raise DomainStateError(
                        "Current firm wallet address must have a confirmed zero balance"
                    )
                if command.active_from <= current.active_from:
                    raise DomainValidationError(
                        "New address activation must be later than current activation"
                    )
                await repository.close_address(current.id, active_to=command.active_from)

            created = await repository.insert_address(
                network=network,
                address=address,
                active_from=command.active_from,
                reason=reason,
                created_by=command.actor_user_id,
            )
            await unit_of_work.commit()
            return created
