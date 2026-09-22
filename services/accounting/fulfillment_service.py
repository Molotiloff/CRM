from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from domain import CurrencyCode, DealStatus, DomainStateError
from services.accounting.firm_position_service import FirmPositionAccountingService
from services.accounting.models import RecordSale, WalletFactSource
from services.crm.deal_service import DealCreateCommand, DealStatusCommand
from services.unit_of_work import UnitOfWorkFactory, UnitOfWorkPort

from .fulfillment_models import (
    EnqueueFulfillment,
    FulfillmentExecutionStarted,
    FulfillmentQueueItem,
    FulfillmentQueueSummary,
    FulfillmentRequestKind,
    FulfillmentStatus,
    ReorderFulfillment,
    StartFulfillmentExecution,
)

log = logging.getLogger("fulfillment_queue")


class FulfillmentQueueError(RuntimeError):
    pass


class FulfillmentQueueService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        position_service: FirmPositionAccountingService | None = None,
        default_city: str = "екб",
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._positions = position_service or FirmPositionAccountingService(
            unit_of_work_factory
        )
        self._default_city = default_city

    async def enqueue(self, command: EnqueueFulfillment) -> FulfillmentQueueItem:
        async with self._unit_of_work_factory() as unit_of_work:
            item = await unit_of_work.fulfillment_queue.enqueue(command)
            await unit_of_work.commit()
            return item

    async def list_active(self) -> list[FulfillmentQueueItem]:
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.fulfillment_queue.list_active()

    async def reorder(self, command: ReorderFulfillment) -> FulfillmentQueueItem:
        async with self._unit_of_work_factory() as unit_of_work:
            item = await unit_of_work.fulfillment_queue.reorder(command)
            await unit_of_work.commit()
            return item

    async def summary(self) -> FulfillmentQueueSummary:
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.fulfillment_queue.summary()

    async def cancel(self, *, item_id: int, reason: str) -> FulfillmentQueueItem:
        async with self._unit_of_work_factory() as unit_of_work:
            item = await unit_of_work.fulfillment_queue.cancel(
                item_id=item_id,
                reason=reason,
            )
            await unit_of_work.commit()
            return item

    async def start_execution(
        self,
        command: StartFulfillmentExecution,
    ) -> FulfillmentExecutionStarted:
        await self._ensure_client_withdrawal(command)
        async with self._unit_of_work_factory() as unit_of_work:
            currency = CurrencyCode("USDT")
            await unit_of_work.wallet_facts.acquire_fact_lock(currency)
            item = await unit_of_work.fulfillment_queue.next_for_client_for_update(
                chat_id=command.chat_id
            )
            if item is None:
                raise FulfillmentQueueError("В очереди клиента нет заявки «На откупе».")
            watch_id = await unit_of_work.payment_watches.create_payment_watch(
                chat_id=command.chat_id,
                chat_name=command.chat_name,
                reply_message_id=command.reply_message_id,
                address=command.address,
                our_address=command.our_address,
                created_by_user_id=command.actor_user_id,
                mode=command.mode,
                phase=command.phase,
                status="WATCHING",
                timeout_at=command.timeout_at,
                deal_id=item.deal_id,
            )
            executing = await unit_of_work.fulfillment_queue.mark_executing(
                item_id=item.id,
                watch_id=watch_id,
            )
            await unit_of_work.commit()
            return FulfillmentExecutionStarted(item=executing, watch_id=watch_id)

    async def _ensure_client_withdrawal(
        self,
        command: StartFulfillmentExecution,
    ) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.fulfillment_queue.acquire_client_lock(command.chat_id)
            existing = await unit_of_work.fulfillment_queue.next_for_client_for_update(
                chat_id=command.chat_id
            )
            if existing is not None:
                await unit_of_work.commit()
                return
            context = await unit_of_work.fulfillment_queue.client_withdrawal_context(
                chat_id=command.chat_id
            )
            if context is None:
                raise FulfillmentQueueError("Клиент не найден для постановки /отпр.")
            if context.qty <= 0:
                raise FulfillmentQueueError("На USDT-балансе клиента нет средств для /отпр.")
            source_ref = f"{command.chat_id}:{command.reply_message_id}"
            deal, _ = await unit_of_work.deals.create_deal_idempotent(
                DealCreateCommand(
                    deal_type="withdrawal",
                    city=self._default_city,
                    actor_user_id=None,
                    client_id=context.client_id,
                    source="tg_bot",
                    source_kind="fulfillment",
                    source_ref=source_ref,
                    body={
                        "request_kind": "client_withdrawal",
                        "recv_code": "USDT",
                        "recv_amount": str(context.qty),
                        "pay_code": "USDT",
                        "pay_amount": str(context.qty),
                        "telegram_client_chat_id": command.chat_id,
                        "telegram_reply_message_id": command.reply_message_id,
                    },
                )
            )
            if deal.status is DealStatus.NEW:
                await unit_of_work.deals.change_status(
                    deal.id,
                    DealStatusCommand(
                        status=DealStatus.AWAITING_PAYMENT,
                        expected_old_status=DealStatus.NEW,
                        actor_user_id=None,
                        payload={"requestKind": "client_withdrawal"},
                    ),
                )
            await unit_of_work.fulfillment_queue.enqueue(
                EnqueueFulfillment(
                    deal_id=deal.id,
                    request_kind=FulfillmentRequestKind.CLIENT_WITHDRAWAL,
                    qty=context.qty,
                    actor_user_id=command.actor_user_id,
                )
            )
            await unit_of_work.commit()

    async def complete_confirmed_in_uow(
        self,
        unit_of_work: UnitOfWorkPort,
        *,
        deal_id: int,
        payment_event_id: int,
        qty: Decimal,
        observed_at: datetime,
        actor_user_id: int | None,
        client_id: int,
        source_firm_wallet: bool,
        allow_quantity_mismatch: bool = False,
    ) -> FulfillmentQueueItem | None:
        item = await unit_of_work.fulfillment_queue.get_by_deal_for_update(
            deal_id=deal_id
        )
        if item is None:
            return None
        if item.status is FulfillmentStatus.COMPLETED:
            if item.payment_event_id != payment_event_id:
                raise DomainStateError("Fulfillment was completed by another event")
            return item
        if item.status is not FulfillmentStatus.EXECUTING:
            raise DomainStateError("Fulfillment is not executing")
        if item.qty != qty and not allow_quantity_mismatch:
            raise DomainStateError("Confirmed quantity differs from fulfillment quantity")

        if item.request_kind is FulfillmentRequestKind.CLIENT_WITHDRAWAL:
            await unit_of_work.transactions.withdraw(
                client_id=client_id,
                currency_code="USDT",
                amount=qty,
                comment=f"confirmed client withdrawal for deal {deal_id}",
                source="payment_watch",
                idempotency_key=f"fulfillment:{item.id}:client_withdrawal",
            )

        position_move_id = None
        wallet_source = "external"
        if source_firm_wallet:
            currency = CurrencyCode("USDT")
            await unit_of_work.wallet_facts.acquire_fact_lock(currency)
            snapshot = await unit_of_work.wallet_facts.latest_snapshot(currency)
            if snapshot is not None and snapshot.actual_qty >= qty:
                updated_fact = snapshot.actual_qty - qty
                await unit_of_work.wallet_facts.append_snapshot(
                    currency=currency,
                    actual_qty=updated_fact,
                    observed_at=observed_at,
                    source=WalletFactSource.PAYMENT_WATCH,
                    address_id=snapshot.address_id,
                    actor_user_id=actor_user_id,
                    comment=f"confirmed fulfillment {item.id}",
                    idempotency_key=f"fulfillment:{item.id}:wallet_fact",
                )
            else:
                log.warning(
                    "Skipping stale USDT wallet fact update for fulfillment %s: "
                    "snapshot=%s transfer=%s",
                    item.id,
                    snapshot.actual_qty if snapshot is not None else None,
                    qty,
                )
            position_move = await self._positions.record_sale(
                RecordSale(
                    currency=currency,
                    qty=qty,
                    deal_id=deal_id,
                    actor_user_id=actor_user_id,
                    effective_at=observed_at,
                    idempotency_key=f"fulfillment:{item.id}:position_sale",
                ),
                unit_of_work=unit_of_work,
            )
            position_move_id = position_move.id
            wallet_source = "firm_wallet"
        return await unit_of_work.fulfillment_queue.complete(
            item_id=item.id,
            payment_event_id=payment_event_id,
            position_move_id=position_move_id,
            actor_user_id=actor_user_id,
            wallet_source=wallet_source,
        )
