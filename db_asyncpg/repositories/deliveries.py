from __future__ import annotations

from decimal import Decimal

from domain import Deal, DomainStateError, DomainValidationError
from services.crm.deal_service import DealCreateCommand, DealNotFoundError, DealStatusCommand
from services.crm.delivery_service import CreateDelivery, delivery_money

from .base import ConnectionBoundRepo
from .cash_settlements import CashSettlementRepo
from .deals import DealRepository
from .transactions import TransactionsRepo


class DeliveryRepository(ConnectionBoundRepo):
    async def couriers(self) -> list[dict[str, str | None]]:
        async with self._connection() as con:
            rows = await con.fetch("""SELECT m.user_id, COALESCE(NULLIF(m.display_name,''), m.user_id::text) AS name,
                w.client_id FROM managers m LEFT JOIN delivery_courier_wallets w ON w.tg_user_id=m.user_id
                ORDER BY LOWER(m.display_name), m.user_id""")
        return [
            {
                "id": str(row["user_id"]),
                "name": row["name"],
                "clientId": str(row["client_id"]) if row["client_id"] else None,
            }
            for row in rows
        ]

    async def create(self, command: CreateDelivery) -> tuple[Deal, bool]:
        async with self._connection() as con:
            async with con.transaction():
                # Pin identity explicitly. Never guess the wallet from a matching name.
                manager = await con.fetchrow(
                    "SELECT display_name FROM managers WHERE user_id=$1 FOR SHARE",
                    command.courier_tg_user_id,
                )
                if manager is None:
                    raise DomainValidationError("Курьер не найден в списке менеджеров")
                for client_id in sorted({command.client_id, command.courier_client_id}):
                    active = await con.fetchval(
                        "SELECT is_active AND COALESCE(client_group,'') <> 'internal_wallet' FROM clients WHERE id=$1 FOR SHARE",
                        client_id,
                    )
                    if not active:
                        raise DomainValidationError("Клиент или чат баланса курьера неактивен")
                cash = await con.fetchval(
                    """SELECT client_id FROM cash_chat_registry WHERE is_active
                    AND cash_currency_codes IS NULL AND LOWER(BTRIM(city))=$1""",
                    command.city,
                )
                if cash is None or cash in {command.client_id, command.courier_client_id}:
                    raise DomainValidationError(
                        "Для доставки нужна отдельная активная касса города"
                    )
                body = {
                    "amount": str(command.amount),
                    "currency": "RUB",
                    "courier_user_id": command.courier_tg_user_id,
                    "courier_client_id": command.courier_client_id,
                    "courier_reward": str(command.reward),
                    "address": command.address,
                }
                repository = DealRepository(self._pool, connection=con)
                deal, created = await repository.create_deal_idempotent(
                    DealCreateCommand(
                        deal_type="delivery",
                        city=command.city,
                        client_id=command.client_id,
                        actor_user_id=command.actor_user_id,
                        source_kind="delivery",
                        source_ref=f"delivery:{command.actor_user_id}:{command.idempotency_key}",
                        body=body,
                        comment=command.comment,
                    )
                )
                if not created:
                    if (
                        any(deal.body.get(key) != value for key, value in body.items())
                        or str(deal.city) != command.city
                        or deal.client_id != command.client_id
                        or deal.comment != command.comment
                    ):
                        raise DomainStateError(
                            "Идентификатор доставки уже использован для другой заявки"
                        )
                    return deal, False
                name = manager["display_name"] or str(command.courier_tg_user_id)
                wallet_name = await con.fetchval(
                    "SELECT name FROM clients WHERE id=$1", command.courier_client_id
                )
                await con.execute(
                    "UPDATE deals SET body=body || jsonb_build_object('courier_name',$2::text,'courier_client_name',$3::text) WHERE id=$1",
                    deal.id,
                    name,
                    wallet_name,
                )
                await con.execute(
                    """INSERT INTO delivery_courier_wallets(tg_user_id,client_id,updated_by)
                    VALUES($1,$2,$3) ON CONFLICT(tg_user_id) DO UPDATE SET client_id=EXCLUDED.client_id,
                    updated_by=EXCLUDED.updated_by,updated_at=NOW()""",
                    command.courier_tg_user_id,
                    command.courier_client_id,
                    command.actor_user_id,
                )
                result = await repository.get_deal(deal.id)
                assert result is not None
                return result, True

    async def transition(
        self,
        deal_id: int,
        *,
        actor_user_id: int,
        status: str,
        expected_amount: Decimal,
        expected_reward: Decimal,
        expected_courier_client_id: int,
    ) -> tuple[Deal, bool]:
        async with self._connection() as con:
            async with con.transaction():
                if not await con.fetchval("SELECT id FROM deals WHERE id=$1 FOR UPDATE", deal_id):
                    raise DealNotFoundError(f"Deal {deal_id} was not found")
                repository = DealRepository(self._pool, connection=con)
                deal = await repository.get_deal(deal_id)
                if deal is None or str(deal.source_kind) != "delivery" or str(deal.source) != "crm":
                    raise DomainValidationError("Это не доставка CRM")
                amount = delivery_money(Decimal(str(deal.body.get("amount"))))
                reward = delivery_money(
                    Decimal(str(deal.body.get("courier_reward"))), positive=False
                )
                courier_id = int(deal.body.get("courier_client_id"))
                if (amount, reward, courier_id) != (
                    expected_amount,
                    expected_reward,
                    expected_courier_client_id,
                ):
                    raise DomainStateError("Параметры доставки изменились; обновите страницу")
                if str(deal.status) == status:
                    return deal, False
                if str(deal.status) not in {"new", "in_delivery"}:
                    raise DomainStateError("Завершённую или отменённую доставку нельзя изменить")
                if status not in {"in_delivery", "done", "canceled"}:
                    raise DomainValidationError("Недопустимый статус доставки")
                patch: dict = {}
                if status == "done":
                    cash_id = await con.fetchval(
                        """SELECT client_id FROM cash_chat_registry WHERE is_active
                        AND cash_currency_codes IS NULL AND LOWER(BTRIM(city))=$1 FOR SHARE""",
                        str(deal.city),
                    )
                    if cash_id is None or cash_id in {deal.client_id, courier_id}:
                        raise DomainStateError("Активная касса доставки не найдена")
                    # Lock all parties in a stable order, also when courier/customer share a wallet.
                    for client_id in sorted({cash_id, deal.client_id, courier_id}):
                        if not await con.fetchval(
                            "SELECT is_active FROM clients WHERE id=$1 FOR SHARE", client_id
                        ):
                            raise DomainStateError("Один из участников доставки неактивен")
                        account = await con.fetchrow(
                            "SELECT balance, precision FROM client_accounts WHERE client_id=$1 AND currency_code='RUB' AND is_active FOR UPDATE",
                            client_id,
                        )
                        if account is None or account["precision"] < 2:
                            raise DomainStateError(
                                "У клиента, кассы и курьера должны быть активные счета RUB с точностью до копеек"
                            )
                    balance = await CashSettlementRepo(
                        self._pool, connection=con
                    ).get_cash_balance_for_update(client_id=cash_id, currency="RUB")
                    if balance is None or balance < amount:
                        raise DomainStateError("Недостаточно RUB в кассе для доставки")
                    ledger = TransactionsRepo(self._pool, connection=con)
                    comment = f"{deal.body.get('courier_name')} доставка по заявке {deal.deal_no}"
                    cash_tx = await ledger.withdraw(
                        client_id=cash_id,
                        currency_code="RUB",
                        amount=amount,
                        source="delivery",
                        comment=comment,
                        idempotency_key=f"delivery:{deal_id}:cash",
                    )
                    client_tx = await ledger.withdraw(
                        client_id=deal.client_id,
                        currency_code="RUB",
                        amount=amount,
                        source="delivery",
                        comment=comment,
                        idempotency_key=f"delivery:{deal_id}:client",
                    )
                    reward_tx = expense_id = None
                    if reward:
                        reward_tx = await ledger.deposit(
                            client_id=courier_id,
                            currency_code="RUB",
                            amount=reward,
                            source="delivery",
                            comment=comment,
                            idempotency_key=f"delivery:{deal_id}:reward",
                        )
                        # This is the expense journal, not an additional cash withdrawal.
                        expense_id = await con.fetchval(
                            """INSERT INTO expenses(kind,category,city,amount,currency_code,comment,created_by,idempotency_key)
                            VALUES('variable','Доставка',$1,$2,'RUB',$3,$4,$5) RETURNING id""",
                            str(deal.city),
                            reward,
                            comment,
                            actor_user_id,
                            f"delivery:{deal_id}:reward",
                        )
                    await con.execute(
                        """INSERT INTO delivery_postings(deal_id,courier_client_id,amount_rub,reward_rub,
                        cash_transaction_id,client_transaction_id,reward_transaction_id,expense_id,posted_by)
                        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                        deal_id,
                        courier_id,
                        amount,
                        reward,
                        cash_tx,
                        client_tx,
                        reward_tx,
                        expense_id,
                        actor_user_id,
                    )
                    patch = {
                        "delivery_posted": True,
                        "expense_id": expense_id,
                        "reward_transaction_id": reward_tx,
                    }
                result = await repository.change_status(
                    deal_id,
                    DealStatusCommand(
                        status=status,
                        expected_old_status=deal.status,
                        actor_user_id=actor_user_id,
                        body_patch=patch,
                    ),
                )
                assert result is not None
                return result[0], True
