from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from domain import Deal, DomainStateError, DomainValidationError
from services.crm.deal_service import DealCreateCommand, DealNotFoundError, DealStatusCommand
from services.crm.invoice_service import CreateInvoice, invoice_amount

from .act_counter import ActCounterRepo
from .base import ConnectionBoundRepo
from .deals import DealRepository
from .profit_accruals import ProfitAccrualsRepo


class InvoiceRepository(ConnectionBoundRepo):
    def __init__(self, pool, *, request_chat_id: int | None = None, connection=None) -> None:
        super().__init__(pool, connection=connection)
        self._request_chat_id = request_chat_id

    async def create(self, command: CreateInvoice) -> tuple[Deal, bool]:
        async with self._connection() as con:
            async with con.transaction():
                if not await con.fetchval("SELECT is_active FROM clients WHERE id=$1", command.client_id):
                    raise DomainValidationError("Клиент не найден или неактивен")
                body = {"currency": "USDT", "received_usdt": str(command.received), "paid_usdt": str(command.paid), "profit_usdt": str(command.received-command.paid)}
                deal, created = await DealRepository(self._pool, connection=con).create_deal_idempotent(DealCreateCommand(
                    deal_type="invoice", city=command.city, client_id=command.client_id,
                    actor_user_id=command.actor_user_id, source_kind="invoice",
                    source_ref=f"invoice:{command.actor_user_id}:{command.idempotency_key}",
                    body=body, comment=command.comment,
                ))
                if not created and (any(deal.body.get(key) != value for key, value in body.items()) or str(deal.city) != command.city.strip() or deal.client_id != command.client_id or deal.comment != command.comment):
                    raise DomainStateError("Идентификатор инвойса уже использован для другой заявки")
                return deal, created

    async def _locked(self, con, deal_id: int) -> tuple[DealRepository, Deal]:
        if not await con.fetchval("SELECT id FROM deals WHERE id=$1 FOR UPDATE", deal_id):
            raise DealNotFoundError(f"Deal {deal_id} was not found")
        repository = DealRepository(self._pool, connection=con)
        deal = await repository.get_deal(deal_id)
        if deal is None or str(deal.deal_type) != "invoice" or str(deal.source_kind) != "invoice" or str(deal.source) != "crm":
            raise DomainValidationError("Это не инвойс CRM")
        return repository, deal

    async def post(self, deal_id: int, *, actor_user_id: int, expected_received: Decimal, expected_paid: Decimal, allow_loss: bool) -> tuple[Deal, bool]:
        async with self._connection() as con:
            async with con.transaction():
                repository, deal = await self._locked(con, deal_id)
                received = invoice_amount(Decimal(str(deal.body.get("received_usdt"))))
                paid = invoice_amount(Decimal(str(deal.body.get("paid_usdt"))))
                if (received, paid) != (expected_received, expected_paid):
                    raise DomainStateError("Суммы инвойса изменились; обновите страницу и подтвердите заново")
                if str(deal.status) == "done" and deal.body.get("invoice_posted"):
                    return deal, False
                if str(deal.status) != "new":
                    raise DomainStateError("Провести можно только новую заявку инвойса")
                profit = received - paid
                if profit < 0 and not allow_loss:
                    raise DomainStateError("Инвойс убыточный. Подтвердите проведение с убытком")
                if self._request_chat_id is None:
                    raise DomainStateError("Для проведения инвойсов настройте REQUEST_CHAT_ID — чат с командой /акт")
                if deal.client_id is None:
                    raise DomainValidationError("Клиент инвойса не найден")
                posting = await ActCounterRepo(self._pool, connection=con).apply_invoice_profit(
                    request_chat_id=self._request_chat_id, invoice_client_id=deal.client_id,
                    deal_id=deal_id, delta=profit, actor_user_id=actor_user_id,
                )
                now = datetime.now(UTC)
                accrual = await ProfitAccrualsRepo(self._pool, connection=con).append(deal_id=deal_id, qty=profit,
                    settlement_status="received", received_at=now, idempotency_key=f"invoice:{deal_id}:profit", cash_only=True)
                # Cash receives only the net movement. The profit journal describes that same movement.
                result = await repository.change_status(deal_id, DealStatusCommand(status="done", expected_old_status="new", actor_user_id=actor_user_id,
                    body_patch={"invoice_posted": True, "posted_at": now.isoformat(), "cash_basis": "act", "act_chat_id": self._request_chat_id,
                        "act_account_id": posting.account_id, "act_transaction_id": posting.transaction_id,
                        "cash_before_usdt": str(posting.before), "cash_after_usdt": str(posting.after), "profit_accrual_id": accrual.id},
                    payload={"receivedUsdt": str(received), "paidUsdt": str(paid), "profitUsdt": str(profit), "lossConfirmed": profit < 0 and allow_loss}))
                assert result is not None
                return result[0], True

    async def cancel(self, deal_id: int, *, actor_user_id: int, comment: str | None) -> tuple[Deal, bool]:
        async with self._connection() as con:
            async with con.transaction():
                repository, deal = await self._locked(con, deal_id)
                if str(deal.status) == "canceled":
                    return deal, False
                if str(deal.status) != "new":
                    raise DomainStateError("Проведённый инвойс нельзя отменить обычной отменой")
                result = await repository.change_status(deal_id, DealStatusCommand(status="canceled", expected_old_status="new", actor_user_id=actor_user_id, payload={"comment": comment}))
                assert result is not None
                return result[0], True
