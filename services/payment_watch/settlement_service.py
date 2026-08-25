from __future__ import annotations

from decimal import Decimal

from domain import DealStatus, SettlementReviewStatus, compare_settlement
from domain.accounting_flows import SettlementResolution
from services.accounting.fulfillment_models import FulfillmentRequestKind
from services.accounting.fulfillment_service import FulfillmentQueueService
from services.crm.deal_service import DealCreateCommand, DealStatusCommand
from services.crm.telegram_deal_registrar import exchange_type_from_firm_perspective
from services.exchange.ledger import (
    apply_ledger_commands,
    plan_cancel_ledger,
    plan_create_ledger,
)
from services.unit_of_work import UnitOfWorkFactory

from .settlement_models import (
    ConfirmedTransfer,
    ReplacementExchange,
    ResolveSettlementCommand,
    SettlementContext,
    SettlementResult,
    SettlementReviewContext,
)


class SettlementError(RuntimeError):
    pass


class DealSettlementService:
    """Reconciles one confirmed blockchain fact with one contractual deal leg."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        fulfillment_queue_service: FulfillmentQueueService | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._fulfillment = fulfillment_queue_service

    async def settle(self, transfer: ConfirmedTransfer) -> SettlementResult:
        async with self._unit_of_work_factory() as unit_of_work:
            context = await unit_of_work.settlements.get_context_for_update(
                watch_id=transfer.watch_id
            )
            if context is None:
                raise SettlementError("Payment watch is not linked to an exchange deal")

            fulfillment = await unit_of_work.fulfillment_queue.get_by_deal_for_update(
                deal_id=context.deal_id
            )
            expected = (
                fulfillment.qty
                if fulfillment is not None
                else self._expected_quantity(context, transfer)
            )
            claim = await unit_of_work.settlements.claim_main_event(transfer)
            if not claim.created:
                existing = await unit_of_work.settlements.get_by_event(
                    event_id=claim.event_id
                )
                if existing is None:
                    raise SettlementError("Claimed payment event has no settlement")
                await unit_of_work.commit()
                return existing

            comparison = compare_settlement(expected=expected, actual=transfer.amount)
            result = await unit_of_work.settlements.create(
                context=context,
                transfer=transfer,
                event_id=claim.event_id,
                expected=comparison.expected,
                actual=comparison.actual,
                review_status=str(comparison.status),
            )
            if comparison.status is SettlementReviewStatus.NEEDS_REVIEW:
                if (
                    fulfillment is None
                    or fulfillment.request_kind
                    is not FulfillmentRequestKind.CLIENT_WITHDRAWAL
                ):
                    await self._apply_actual_delta(
                        unit_of_work.transactions,
                        context=context,
                        transfer=transfer,
                        delta=comparison.delta,
                        event_id=claim.event_id,
                    )
                await unit_of_work.settlements.enqueue_review_notification(
                    context=context,
                    result=result,
                    tx_hash=transfer.tx_hash,
                )
            else:
                if self._fulfillment is not None:
                    await self._fulfillment.complete_confirmed_in_uow(
                        unit_of_work,
                        deal_id=context.deal_id,
                        payment_event_id=claim.event_id,
                        qty=transfer.amount,
                        observed_at=transfer.block_ts,
                        actor_user_id=None,
                        client_id=context.client_id,
                        source_firm_wallet=transfer.direction == "IN",
                    )
            if (
                comparison.status is SettlementReviewStatus.MATCHED
                and context.deal_status == str(DealStatus.AWAITING_PAYMENT)
            ):
                await unit_of_work.deals.change_status(
                    context.deal_id,
                    DealStatusCommand(
                        status=DealStatus.DONE,
                        expected_old_status=DealStatus.AWAITING_PAYMENT,
                        actor_user_id=None,
                        payload={
                            "paymentWatchId": transfer.watch_id,
                            "txHash": transfer.tx_hash,
                            "paymentAmount": str(transfer.amount),
                            "paymentCurrency": transfer.token_symbol.upper(),
                            "settlementId": result.settlement_id,
                        },
                        body_patch={"payment_tx_hash": transfer.tx_hash},
                        tronscan_url=(
                            "https://tronscan.org/#/transaction/" + transfer.tx_hash
                        ),
                    ),
                )
            await unit_of_work.settlements.complete_watch(watch_id=transfer.watch_id)
            await unit_of_work.commit()
            return result

    async def resolve_review(self, command: ResolveSettlementCommand) -> SettlementResult:
        async with self._unit_of_work_factory() as unit_of_work:
            context = await unit_of_work.settlements.get_review_for_update(
                settlement_id=command.settlement_id
            )
            if context is None:
                raise SettlementError(f"Settlement {command.settlement_id} was not found")
            if context.result.status is SettlementReviewStatus.RESOLVED:
                if context.result.resolution is command.resolution:
                    await unit_of_work.commit()
                    return context.result
                raise SettlementError("Settlement was resolved with another decision")
            if context.result.status is not SettlementReviewStatus.NEEDS_REVIEW:
                raise SettlementError("Settlement review is no longer pending")

            if command.resolution is SettlementResolution.AMEND_UNPOSTED:
                await unit_of_work.settlements.amend_contract_to_actual(context)
                await self._complete_reviewed_deal(
                    unit_of_work,
                    context=context,
                    command=command,
                )
            elif command.resolution is SettlementResolution.ACCEPT_ACTUAL:
                await self._complete_reviewed_deal(
                    unit_of_work,
                    context=context,
                    command=command,
                )
            elif command.resolution is SettlementResolution.CANCEL_AND_RECREATE:
                if command.replacement is None:
                    raise SettlementError("Replacement terms are required")
                await self._cancel_and_recreate(
                    unit_of_work,
                    context=context,
                    replacement=command.replacement,
                    command=command,
                )
            else:
                raise SettlementError(f"Unsupported resolution: {command.resolution}")

            result = await unit_of_work.settlements.resolve_review(
                settlement_id=command.settlement_id,
                resolution=str(command.resolution),
                actor_user_id=command.actor_user_id,
                comment=command.comment,
            )
            await unit_of_work.commit()
            return result

    async def _complete_reviewed_deal(
        self,
        unit_of_work,
        *,
        context: SettlementReviewContext,
        command: ResolveSettlementCommand,
    ) -> None:
        if self._fulfillment is not None:
            evidence = context.result.evidence
            if evidence is None:
                raise SettlementError("Settlement evidence is unavailable")
            await self._fulfillment.complete_confirmed_in_uow(
                unit_of_work,
                deal_id=context.result.deal_id,
                payment_event_id=context.result.event_id,
                qty=context.result.actual,
                observed_at=evidence.block_ts,
                actor_user_id=command.actor_user_id,
                client_id=context.client_id,
                source_firm_wallet=evidence.direction == "IN",
                allow_quantity_mismatch=True,
            )
        changed = await unit_of_work.deals.change_status(
            context.result.deal_id,
            DealStatusCommand(
                status=DealStatus.DONE,
                expected_old_status=context.deal_status,
                actor_user_id=command.actor_user_id,
                payload={
                    "settlementId": command.settlement_id,
                    "settlementResolution": str(command.resolution),
                    "comment": command.comment,
                },
            ),
        )
        if changed is None:
            raise SettlementError(f"Deal {context.result.deal_id} was not found")

    @staticmethod
    async def _cancel_and_recreate(
        unit_of_work,
        *,
        context: SettlementReviewContext,
        replacement: ReplacementExchange,
        command: ResolveSettlementCommand,
    ) -> None:
        if not context.is_exchange:
            raise SettlementError("Only exchange settlements can be recreated")
        if await unit_of_work.exchange_requests.get_exchange_request_link(
            client_req_id=replacement.request_id
        ) is not None:
            raise SettlementError(
                f"Replacement request {replacement.request_id} already exists"
            )
        if await unit_of_work.exchange_requests.get_exchange_request_link_by_table_req_id(
            table_req_id=replacement.table_request_id
        ) is not None:
            raise SettlementError(
                f"Replacement table request {replacement.table_request_id} already exists"
            )
        settled_recv_leg = (
            context.recv_code == context.settlement_currency
            and context.pay_code != context.settlement_currency
        ) or (
            context.recv_code == context.pay_code == context.settlement_currency
            and context.event_direction == "OUT"
        )
        effective_recv = context.result.actual if settled_recv_leg else context.recv_amount
        effective_pay = context.pay_amount if settled_recv_leg else context.result.actual
        cancel_commands, _, _ = plan_cancel_ledger(
            request_id=context.request_id,
            chat_id=context.result.deal_id,
            message_id=command.settlement_id,
            receive_code=context.recv_code,
            receive_amount=effective_recv,
            pay_code=context.pay_code,
            pay_amount=effective_pay,
            receive_is_deposit=True,
            pay_is_withdraw=True,
            tracked_currency_codes=None,
        )
        await apply_ledger_commands(
            unit_of_work.transactions,
            client_id=context.client_id,
            commands=cancel_commands,
        )
        source_canceled = await unit_of_work.exchange_requests.set_exchange_request_status(
            client_req_id=context.request_id,
            status="cancelled",
        )
        if not source_canceled:
            raise SettlementError(f"Exchange request {context.request_id} was not found")
        canceled = await unit_of_work.deals.change_status(
            context.result.deal_id,
            DealStatusCommand(
                status=DealStatus.CANCELED,
                expected_old_status=context.deal_status,
                actor_user_id=command.actor_user_id,
                payload={
                    "settlementId": command.settlement_id,
                    "settlementResolution": str(command.resolution),
                    "replacementRequestId": replacement.request_id,
                },
            ),
        )
        if canceled is None:
            raise SettlementError(f"Deal {context.result.deal_id} was not found")

        create_commands = plan_create_ledger(
            receive_code=replacement.recv_code,
            receive_amount=replacement.recv_amount,
            receive_comment=command.comment or "settlement review replacement",
            pay_code=replacement.pay_code,
            pay_amount=replacement.pay_amount,
            pay_comment=command.comment or "settlement review replacement",
            receive_is_deposit=True,
            pay_is_withdraw=True,
            receive_idempotency_key=f"settlement:{command.settlement_id}:replacement:recv",
            pay_idempotency_key=f"settlement:{command.settlement_id}:replacement:pay",
            tracked_currency_codes=None,
        )
        await apply_ledger_commands(
            unit_of_work.transactions,
            client_id=context.client_id,
            commands=create_commands,
        )
        await unit_of_work.exchange_requests.upsert_exchange_request_link(
            client_req_id=replacement.request_id,
            table_req_id=replacement.table_request_id,
            table_in_cur=replacement.recv_code,
            table_out_cur=replacement.pay_code,
            table_in_amount=replacement.recv_amount,
            table_out_amount=replacement.pay_amount,
            table_rate=replacement.rate,
            status="active",
        )
        replacement_body = dict(context.body)
        replacement_body.update(
            {
                "client_req_id": replacement.request_id,
                "table_req_id": replacement.table_request_id,
                "recv_code": replacement.recv_code,
                "recv_amount": str(replacement.recv_amount),
                "pay_code": replacement.pay_code,
                "pay_amount": str(replacement.pay_amount),
                "rate": str(replacement.rate),
                "replaces_deal_id": context.result.deal_id,
                "settlement_id": command.settlement_id,
            }
        )
        await unit_of_work.deals.create_deal_idempotent(
            DealCreateCommand(
                deal_type=exchange_type_from_firm_perspective(
                    replacement.recv_code,
                    replacement.pay_code,
                ),
                city=context.city,
                actor_user_id=command.actor_user_id,
                client_id=context.client_id,
                source="tg_bot",
                source_kind="exchange",
                source_ref=(
                    f"{context.source_ref}:settlement-recreate:{command.settlement_id}"
                ),
                exchange_client_req_id=replacement.request_id,
                comment=command.comment,
                body=replacement_body,
            )
        )

    @staticmethod
    def _expected_quantity(
        context: SettlementContext,
        transfer: ConfirmedTransfer,
    ) -> Decimal:
        if transfer.direction not in {"IN", "OUT"}:
            raise SettlementError(f"Unsupported payment direction: {transfer.direction}")
        token = transfer.token_symbol.upper()
        recv_matches = context.recv_code == token
        pay_matches = context.pay_code == token
        if not recv_matches and not pay_matches:
            raise SettlementError(
                f"Payment currency {transfer.token_symbol} does not match either deal leg"
            )
        if recv_matches != pay_matches:
            return context.recv_amount if recv_matches else context.pay_amount
        return context.pay_amount if transfer.direction == "IN" else context.recv_amount

    @staticmethod
    async def _apply_actual_delta(
        transactions,
        *,
        context: SettlementContext,
        transfer: ConfirmedTransfer,
        delta: Decimal,
        event_id: int,
    ) -> None:
        if delta == 0:
            return
        # Contractual legs are already posted at request creation. Only the delta
        # is applied here, so blockchain evidence can never repeat the full leg.
        token = transfer.token_symbol.upper()
        recv_leg = context.recv_code == token and context.pay_code != token
        if context.recv_code == context.pay_code == token:
            recv_leg = transfer.direction == "OUT"
        expected_action_is_deposit = recv_leg
        use_deposit = expected_action_is_deposit == (delta > 0)
        operation = transactions.deposit if use_deposit else transactions.withdraw
        await operation(
            client_id=context.client_id,
            currency_code=transfer.token_symbol,
            amount=abs(delta),
            comment=f"settlement actual delta; tx={transfer.tx_hash}",
            source="payment_watch_settlement",
            idempotency_key=f"settlement:{event_id}:actual_delta",
        )
