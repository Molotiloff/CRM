from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from domain import DealStatus, DomainStateError, DomainValidationError
from services.accounting.firm_position_service import FirmPositionAccountingService
from services.accounting.models import RecordSale
from services.crm.deal_service import DealStatusCommand
from services.unit_of_work import UnitOfWorkFactory

from .cash_settlement_models import (
    CashSettlementCommand,
    CashSettlementContext,
    CashSettlementResult,
)


class CashSettlementError(DomainStateError):
    pass


class CashSettlementService:
    _POSITION_CURRENCY = {
        "USD": "USD_BL",
        "USDW": "USD_WH",
        "EUR": "EUR",
        "USDT": "USDT",
    }

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        position_service: FirmPositionAccountingService | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._positions = position_service or FirmPositionAccountingService(
            unit_of_work_factory
        )

    async def mark_ready(
        self,
        *,
        request_id: str,
        actor_tg_user_id: int | None,
    ) -> int:
        async with self._unit_of_work_factory() as unit_of_work:
            context = await unit_of_work.cash_settlements.get_request_for_update(
                request_id=request_id
            )
            if context is None:
                raise CashSettlementError(f"Кассовая заявка {request_id} не найдена.")
            if context.deal_status is DealStatus.READY_FOR_CASH_SETTLEMENT:
                await unit_of_work.commit()
                return context.deal_id
            if context.deal_status is not DealStatus.NEW:
                raise CashSettlementError(
                    f"Заявка {request_id} имеет статус {context.deal_status}."
                )
            changed = await unit_of_work.deals.change_status(
                context.deal_id,
                DealStatusCommand(
                    status=DealStatus.READY_FOR_CASH_SETTLEMENT,
                    expected_old_status=DealStatus.NEW,
                    actor_user_id=None,
                    payload={
                        "requestId": request_id,
                        "actorTgUserId": actor_tg_user_id,
                    },
                ),
            )
            if changed is None:
                raise CashSettlementError(f"Сделка заявки {request_id} не найдена.")
            await unit_of_work.commit()
            return context.deal_id

    async def is_settled(self, *, request_id: str) -> bool:
        async with self._unit_of_work_factory() as unit_of_work:
            return (
                await unit_of_work.cash_settlements.get_by_request(
                    request_id=request_id
                )
                is not None
            )

    async def cancel_request(
        self,
        *,
        request_id: str,
        actor_tg_user_id: int | None,
    ) -> int:
        async with self._unit_of_work_factory() as unit_of_work:
            context = await unit_of_work.cash_settlements.get_request_for_update(
                request_id=request_id
            )
            if context is None:
                raise CashSettlementError(f"Кассовая заявка {request_id} не найдена.")
            if await unit_of_work.cash_settlements.get_by_request(
                request_id=request_id
            ) is not None:
                raise CashSettlementError("Проведенную кассовую заявку нельзя отменить.")
            if context.deal_status is DealStatus.CANCELED:
                await unit_of_work.commit()
                return context.deal_id
            if context.deal_status not in {
                DealStatus.NEW,
                DealStatus.READY_FOR_CASH_SETTLEMENT,
            }:
                raise CashSettlementError(
                    f"Заявка {request_id} имеет статус {context.deal_status}."
                )
            changed = await unit_of_work.deals.change_status(
                context.deal_id,
                DealStatusCommand(
                    status=DealStatus.CANCELED,
                    expected_old_status=context.deal_status,
                    actor_user_id=None,
                    payload={
                        "requestId": request_id,
                        "actorTgUserId": actor_tg_user_id,
                    },
                ),
            )
            if changed is None:
                raise CashSettlementError(f"Сделка заявки {request_id} не найдена.")
            await unit_of_work.request_schedule.deactivate_request_schedule_entry(
                request_id
            )
            await unit_of_work.commit()
            return context.deal_id

    async def settle(self, command: CashSettlementCommand) -> CashSettlementResult:
        request_id = command.request_id.strip()
        currency = command.currency.strip().upper()
        signed_qty = Decimal(command.signed_qty)
        if not request_id:
            raise DomainValidationError("Cash settlement request id is required")
        if not signed_qty.is_finite() or signed_qty == 0:
            raise DomainValidationError("Cash settlement quantity must be non-zero")

        async with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.cash_settlements
            await repository.acquire_request_lock(request_id=request_id)
            repeated = await repository.get_by_request(request_id=request_id)
            if repeated is not None:
                self._validate_repeated(
                    repeated,
                    currency=currency,
                    signed_qty=signed_qty,
                )
                await unit_of_work.commit()
                return replace(repeated, repeated=True)

            context = await repository.get_context_for_update(
                request_id=request_id,
                city_chat_id=command.city_chat_id,
            )
            if context is None:
                raise CashSettlementError(
                    f"Заявка {request_id} не найдена в активной кассе этого города."
                )
            self._validate_command(context, currency=currency, signed_qty=signed_qty)
            actual_qty = abs(signed_qty)
            operation = (
                unit_of_work.transactions.deposit
                if context.request_kind == "dep"
                else unit_of_work.transactions.withdraw
            )
            cash_transaction_id = await operation(
                client_id=context.cash_client_id,
                currency_code=currency,
                amount=actual_qty,
                comment=f"cash settlement {request_id}",
                source="cash_settlement",
                idempotency_key=f"cash_settlement:{request_id}:cash",
            )
            client_transaction_id = None
            if context.track_client_balance:
                client_transaction_id = await operation(
                    client_id=context.client_id,
                    currency_code=currency,
                    amount=actual_qty,
                    comment=f"cash settlement {request_id}",
                    source="cash_settlement",
                    idempotency_key=f"cash_settlement:{request_id}:client",
                )

            position_move_id = None
            position_currency = self._POSITION_CURRENCY.get(currency)
            if context.request_kind == "wd" and position_currency is not None:
                move = await self._positions.record_sale(
                    RecordSale(
                        currency=position_currency,
                        qty=actual_qty,
                        deal_id=context.deal_id,
                        actor_user_id=None,
                        effective_at=datetime.now(UTC),
                        idempotency_key=f"cash_settlement:{request_id}:position",
                    ),
                    unit_of_work=unit_of_work,
                )
                position_move_id = move.id

            result = await repository.insert(
                context=context,
                command=command,
                actual_qty=actual_qty,
                cash_transaction_id=cash_transaction_id,
                client_transaction_id=client_transaction_id,
                position_move_id=position_move_id,
            )
            await unit_of_work.request_schedule.deactivate_request_schedule_entry(
                request_id
            )
            changed = await unit_of_work.deals.change_status(
                context.deal_id,
                DealStatusCommand(
                    status=DealStatus.DONE,
                    expected_old_status=DealStatus.READY_FOR_CASH_SETTLEMENT,
                    actor_user_id=None,
                    payload={
                        "requestId": request_id,
                        "cashSettlementId": result.settlement_id,
                        "actualQty": str(actual_qty),
                        "currency": currency,
                        "actorTgUserId": command.actor_tg_user_id,
                        "evidence": command.evidence,
                    },
                    body_patch={
                        "cash_settlement_id": result.settlement_id,
                        "settled_qty": str(actual_qty),
                    },
                ),
            )
            if changed is None:
                raise CashSettlementError(f"Сделка заявки {request_id} не найдена.")
            await unit_of_work.commit()
            return result

    @staticmethod
    def _validate_command(
        context: CashSettlementContext,
        *,
        currency: str,
        signed_qty: Decimal,
    ) -> None:
        if context.deal_status is not DealStatus.READY_FOR_CASH_SETTLEMENT:
            raise CashSettlementError(
                "Сначала нажмите «Готово к расчету» в карточке заявки."
            )
        if currency != context.currency:
            raise CashSettlementError(
                f"В заявке {context.currency}, а команда использует {currency}."
            )
        expected_positive = context.request_kind == "dep"
        if (signed_qty > 0) != expected_positive:
            action = "положительной" if expected_positive else "отрицательной"
            raise CashSettlementError(f"Сумма для этой заявки должна быть {action}.")

    @staticmethod
    def _validate_repeated(
        result: CashSettlementResult,
        *,
        currency: str,
        signed_qty: Decimal,
    ) -> None:
        expected_positive = result.request_kind == "dep"
        if (
            result.currency != currency
            or result.actual_qty != abs(signed_qty)
            or (signed_qty > 0) != expected_positive
        ):
            raise CashSettlementError(
                "Заявка уже проведена другой кассовой командой или суммой."
            )
