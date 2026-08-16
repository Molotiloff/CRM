from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from domain import (
    Deal,
    DealEventPayload,
    DealStatus,
    DealTransitionPolicy,
    InvalidDealTransitionError,
    SourceKind,
)

from .deal_service import DealStatusPreparation, DealValidationError


class DealWorkflowRepositoryPort(Protocol):
    async def get_act_balance_check(self, deal_id: int) -> dict[str, Any] | None: ...

    async def resolve_payment_watch(
        self,
        *,
        deal_id: int,
        requested_watch_id: int | None,
    ) -> dict[str, Any] | None: ...

    async def get_completed_payment(self, deal_id: int) -> dict[str, Any] | None: ...


class DealStatusPolicy:
    def __init__(self, repository: DealWorkflowRepositoryPort) -> None:
        self._repository = repository

    @classmethod
    def allowed_transitions(cls, deal: Deal) -> tuple[DealStatus, ...]:
        return DealTransitionPolicy.allowed(deal.status, deal.source_kind)

    async def prepare(
        self,
        deal: Deal,
        *,
        new_status: DealStatus,
        payload: DealEventPayload,
    ) -> DealStatusPreparation:
        old_status = deal.status
        is_exchange = deal.source_kind is SourceKind.EXCHANGE
        self._validate_transition(
            old_status,
            new_status,
            is_exchange=is_exchange,
        )
        enriched = payload.to_dict()

        if new_status is DealStatus.FIXED:
            return self._prepare_fixed(deal, enriched)
        if new_status is DealStatus.BALANCE_CHECK:
            return await self._prepare_balance_check(deal, enriched, is_exchange=is_exchange)
        if new_status is DealStatus.AWAITING_PAYMENT:
            return await self._prepare_awaiting_payment(
                deal,
                enriched,
                require_watch=True,
            )
        body = deal.body
        requires_confirmed_payment = (
            is_exchange
            or old_status is DealStatus.AWAITING_PAYMENT
            or body.get("payment_watch_id") is not None
        )
        if new_status is DealStatus.DONE and requires_confirmed_payment:
            return await self._prepare_done(deal, enriched)
        return DealStatusPreparation(payload=enriched)

    def _prepare_fixed(
        self,
        deal: Deal,
        payload: dict[str, Any],
    ) -> DealStatusPreparation:
        body = deal.body
        raw_rate = payload.get("rate") or payload.get("fixedRate") or body.get("rate")
        try:
            rate = Decimal(str(raw_rate))
        except (InvalidOperation, TypeError, ValueError):
            raise DealValidationError("Fixed status requires a valid rate") from None
        if not rate.is_finite() or rate <= 0:
            raise DealValidationError("Fixed rate must be greater than zero")
        normalized = format(rate.normalize(), "f")
        payload["rate"] = normalized
        return DealStatusPreparation(
            payload=payload,
            body_patch={"fixed_rate": normalized},
        )

    async def _prepare_balance_check(
        self,
        deal: Deal,
        payload: dict[str, Any],
        *,
        is_exchange: bool,
    ) -> DealStatusPreparation:
        body = deal.body
        currencies = {
            str(body.get("recv_code") or "").upper(),
            str(body.get("pay_code") or "").upper(),
        }
        if not is_exchange or "USDT" not in currencies:
            payload["actCheckApplicable"] = False
            return DealStatusPreparation(
                payload=payload,
                body_patch={"insufficient_usdt": False, "act_check_applicable": False},
            )

        result = await self._repository.get_act_balance_check(deal.id)
        if result is None:
            raise DealValidationError("ACT balance data is unavailable for this deal")
        current = Decimal(result["current_amount"])
        required = Decimal(result["required_amount"])
        shortage = Decimal(result["shortage_amount"])
        insufficient = bool(result["insufficient"])
        payload.update(
            {
                "actCheckApplicable": True,
                "actCurrentUsdt": str(current),
                "requiredUsdt": str(required),
                "shortageUsdt": str(shortage),
                "insufficientUsdt": insufficient,
                "requestChatId": int(result["request_chat_id"]),
            }
        )
        return DealStatusPreparation(
            payload=payload,
            body_patch={
                "act_check_applicable": True,
                "act_current_usdt": str(current),
                "required_usdt": str(required),
                "shortage_usdt": str(shortage),
                "insufficient_usdt": insufficient,
            },
        )

    async def _prepare_awaiting_payment(
        self,
        deal: Deal,
        payload: dict[str, Any],
        *,
        require_watch: bool,
    ) -> DealStatusPreparation:
        body = deal.body
        if bool(body.get("insufficient_usdt")):
            raise DealValidationError(
                "ACT balance is insufficient; repeat balance check after replenishment"
            )
        raw_watch_id = payload.get("paymentWatchId") or payload.get("payment_watch_id")
        try:
            requested_watch_id = int(raw_watch_id) if raw_watch_id is not None else None
        except (TypeError, ValueError):
            raise DealValidationError("paymentWatchId must be an integer") from None
        watch = await self._repository.resolve_payment_watch(
            deal_id=deal.id,
            requested_watch_id=requested_watch_id,
        )
        if watch is None:
            if require_watch:
                raise DealValidationError("Active payment watch was not found for this deal")
            return DealStatusPreparation(payload=payload)
        watch_id = int(watch["id"])
        payload.update(
            {
                "paymentWatchId": watch_id,
                "paymentWatchStatus": str(watch["status"]),
            }
        )
        return DealStatusPreparation(
            payload=payload,
            body_patch={"payment_watch_id": watch_id},
            payment_watch_id=watch_id,
        )

    async def _prepare_done(
        self,
        deal: Deal,
        payload: dict[str, Any],
    ) -> DealStatusPreparation:
        payment = await self._repository.get_completed_payment(deal.id)
        if payment is None:
            raise DealValidationError("Main payment is not confirmed yet")
        tx_hash = str(payment["tx_hash"])
        tronscan_url = f"https://tronscan.org/#/transaction/{tx_hash}"
        payload.update(
            {
                "paymentWatchId": int(payment["watch_id"]),
                "txHash": tx_hash,
                "paymentAmount": str(payment["amount"]),
                "paymentCurrency": str(payment["token_symbol"]),
                "tronscanUrl": tronscan_url,
            }
        )
        return DealStatusPreparation(
            payload=payload,
            body_patch={"payment_tx_hash": tx_hash},
            tronscan_url=tronscan_url,
        )

    @classmethod
    def _validate_transition(
        cls,
        old_status: DealStatus,
        new_status: DealStatus,
        *,
        is_exchange: bool,
    ) -> None:
        source_kind = SourceKind.EXCHANGE if is_exchange else None
        try:
            DealTransitionPolicy.validate(old_status, new_status, source_kind)
        except InvalidDealTransitionError as exc:
            raise DealValidationError(str(exc)) from exc
