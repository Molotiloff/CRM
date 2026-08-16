from __future__ import annotations

from domain import Deal, DealSource, DealStatus, SourceKind
from observability import bind_log_context, logged_operation
from services.unit_of_work import UnitOfWorkFactory

from .deal_service import (
    DealNotFoundError,
    DealService,
    DealStatusCommand,
    DealValidationError,
)
from .deal_source_adapter import DealSourceAdapterRegistry
from .deal_source_commands import (
    CashSourceEdit,
    DealSourceEditCommand,
    ExchangeSourceEdit,
)
from .deal_status_policy import DealStatusPolicy

__all__ = [
    "CashSourceEdit",
    "DealSourceMutationService",
    "ExchangeSourceEdit",
]


class DealSourceMutationService:
    def __init__(
        self,
        *,
        deal_service: DealService,
        unit_of_work_factory: UnitOfWorkFactory,
        adapters: DealSourceAdapterRegistry,
    ) -> None:
        self._deals = deal_service
        self._unit_of_work_factory = unit_of_work_factory
        self._adapters = adapters

    @logged_operation("deal.source.cancel")
    async def cancel(
        self,
        deal_id: int,
        *,
        actor_user_id: int,
        comment: str | None = None,
    ) -> Deal:
        bind_log_context(deal_id=deal_id)
        deal = await self._deals.get_deal(deal_id)
        if deal.status is DealStatus.CANCELED:
            return deal
        if DealStatus.CANCELED not in DealStatusPolicy.allowed_transitions(deal):
            raise DealValidationError(f"Deal in status {deal.status} cannot be canceled")
        self._require_tg_source(deal)
        adapter = self._adapters.require(deal.source_kind)
        payload = {"comment": comment} if comment else {}

        async with self._unit_of_work_factory() as unit_of_work:
            await adapter.cancel(unit_of_work, deal)
            result = await unit_of_work.deals.change_status(
                deal_id,
                DealStatusCommand(
                    status=DealStatus.CANCELED,
                    actor_user_id=actor_user_id,
                    payload=payload,
                    expected_old_status=deal.status,
                ),
            )
            if result is None:
                raise DealNotFoundError(f"Deal {deal_id} was not found")
            updated, _ = result
            await unit_of_work.commit()

        await self._deals.publish_committed_status_change(
            updated,
            new_status=DealStatus.CANCELED,
            payload=payload,
        )
        return updated

    @logged_operation("deal.source.edit_exchange")
    async def edit_exchange(
        self,
        deal_id: int,
        command: ExchangeSourceEdit,
        *,
        actor_name: str,
    ) -> Deal:
        bind_log_context(deal_id=deal_id)
        return await self._edit(
            deal_id,
            source_kind=SourceKind.EXCHANGE,
            command=command,
            actor_name=actor_name,
        )

    @logged_operation("deal.source.edit_cash")
    async def edit_cash(
        self,
        deal_id: int,
        command: CashSourceEdit,
        *,
        actor_name: str,
    ) -> Deal:
        bind_log_context(deal_id=deal_id)
        return await self._edit(
            deal_id,
            source_kind=SourceKind.CASH,
            command=command,
            actor_name=actor_name,
        )

    async def _edit(
        self,
        deal_id: int,
        *,
        source_kind: SourceKind,
        command: DealSourceEditCommand,
        actor_name: str,
    ) -> Deal:
        deal = await self._editable_deal(deal_id, source_kind)
        adapter = self._adapters.require(source_kind)
        plan = await adapter.prepare_edit(deal, command, actor_name=actor_name)

        async with self._unit_of_work_factory() as unit_of_work:
            await plan.apply(unit_of_work)
            updated = await unit_of_work.deals.update_deal(
                deal_id,
                plan.update_command,
            )
            if updated is None:
                raise DealNotFoundError(f"Deal {deal_id} was not found")
            await unit_of_work.commit()

        await self._deals.publish_committed_update(updated)
        return updated

    async def _editable_deal(self, deal_id: int, source_kind: SourceKind) -> Deal:
        deal = await self._deals.get_deal(deal_id)
        self._require_tg_source(deal)
        if deal.source_kind is not source_kind:
            raise DealValidationError(f"Deal is not a {source_kind} Telegram request")
        if deal.is_terminal:
            raise DealValidationError(f"Deal in status {deal.status} cannot be edited")
        return deal

    @staticmethod
    def _require_tg_source(deal: Deal) -> None:
        if deal.source is not DealSource.TELEGRAM:
            raise DealValidationError("Only Telegram-backed deals have source operations")
