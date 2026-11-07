from __future__ import annotations

from domain import Deal, SourceKind
from services.unit_of_work import UnitOfWorkPort

from .deal_service import DealValidationError
from .deal_source_adapter import DealSourceEditPlan
from .deal_source_commands import DealSourceEditCommand


class FulfillmentDealSourceAdapter:
    source_kind = SourceKind.FULFILLMENT

    async def prepare_edit(
        self, deal: Deal, command: DealSourceEditCommand, *, actor_name: str,
    ) -> DealSourceEditPlan:
        raise DealValidationError("Изменение заявки /отпр не поддерживается; отмените её и создайте новую")

    async def cancel(self, unit_of_work: UnitOfWorkPort, deal: Deal) -> None:
        # /отпр does not post the main amount until a confirmed payment arrives.
        # Cancel the unexecuted intent, never manufacture a wallet refund.
        await unit_of_work.fulfillment_queue.cancel_for_deal(deal_id=deal.id)
