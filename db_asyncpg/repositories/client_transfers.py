from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

import asyncpg

from domain import DomainStateError, DomainValidationError
from services.crm.client_transfer_models import (
    ClientTransferAdjustmentCommand,
    ClientTransferAdjustmentResult,
    ClientTransferCommand,
    ClientTransferResult,
)


class ClientTransferRepository:
    """Post both client wallet legs and their CRM deal in one transaction."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def find_recipients(self, name: str) -> list[dict[str, int | str]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """SELECT id, chat_id, name FROM clients
                   WHERE is_active AND LOWER(BTRIM(name))=LOWER(BTRIM($1))
                   ORDER BY id""",
                name,
            )
        return [dict(row) for row in rows]

    async def transfer(self, command: ClientTransferCommand) -> ClientTransferResult:
        amount = self._validate(command)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"client-transfer:{command.source}:{command.source_ref}",
                )
                existing = await connection.fetchrow(
                    """SELECT id, body, comment, status, created_at FROM deals
                       WHERE source=$1 AND source_kind='client_transfer' AND source_ref=$2""",
                    command.source,
                    command.source_ref,
                )
                if existing is not None:
                    return await self._repeated(connection, existing, command, amount)

                sender = await self._client(
                    connection, client_id=command.from_client_id, chat_id=command.from_chat_id
                )
                recipient = await self._client(
                    connection, client_id=command.to_client_id, name=command.to_client_name
                )
                if sender["id"] == recipient["id"]:
                    raise DomainValidationError("Нельзя перевести средства самому себе")

                cash_scoped = await connection.fetchval(
                    """SELECT EXISTS (
                           SELECT 1 FROM cash_chat_registry
                           WHERE is_active AND client_id=ANY($1::bigint[])
                             AND (cash_currency_codes IS NULL OR $2=ANY(cash_currency_codes))
                       )""",
                    [sender["id"], recipient["id"]],
                    command.currency.upper(),
                )
                if cash_scoped:
                    raise DomainValidationError(
                        "Этот счёт учитывается как касса, а не как клиентский баланс"
                    )

                accounts = await connection.fetch(
                    """SELECT id, client_id, precision, balance
                       FROM client_accounts
                       WHERE client_id = ANY($1::bigint[]) AND currency_code=$2 AND is_active
                       ORDER BY id FOR UPDATE""",
                    [sender["id"], recipient["id"]],
                    command.currency.upper(),
                )
                by_client = {row["client_id"]: row for row in accounts}
                if sender["id"] not in by_client or recipient["id"] not in by_client:
                    raise DomainValidationError(
                        f"У одного из клиентов нет активного счёта {command.currency.upper()}"
                    )
                debit = by_client[sender["id"]]
                credit = by_client[recipient["id"]]
                if debit["precision"] != credit["precision"]:
                    raise DomainValidationError("Точность счетов отправителя и получателя различается")
                precision = int(debit["precision"])
                quantum = Decimal(1).scaleb(-precision)
                if amount != amount.quantize(quantum):
                    raise DomainValidationError(
                        f"Для {command.currency.upper()} допускается не более {precision} знаков после запятой"
                    )
                from_balance = Decimal(str(debit["balance"])) - amount
                to_balance = Decimal(str(credit["balance"])) + amount
                if from_balance < 0 and not command.allow_negative:
                    raise DomainStateError("Недостаточно средств на счёте отправителя")

                body = {
                    "from_client_id": sender["id"],
                    "to_client_id": recipient["id"],
                    "from_client_name": sender["name"],
                    "to_client_name": recipient["name"],
                    "currency": command.currency.upper(),
                    "amount": str(amount),
                    "actor_tg_user_id": command.actor_tg_user_id,
                    "negative_balance_confirmed": bool(from_balance < 0),
                }
                deal = await connection.fetchrow(
                    """INSERT INTO deals
                         (deal_type, city, client_id, status, created_by, source,
                          source_kind, source_ref, comment, body)
                       VALUES ('client_transfer', $1, $2, 'done', $3, $4,
                               'client_transfer', $5, $6, $7::jsonb)
                       RETURNING id, created_at""",
                    command.city,
                    sender["id"],
                    command.actor_user_id,
                    command.source,
                    command.source_ref,
                    command.comment,
                    json.dumps(body, ensure_ascii=False),
                )
                deal_id = int(deal["id"])
                await connection.execute(
                    """INSERT INTO deal_status_events
                         (deal_id, old_status, new_status, actor_user_id)
                       VALUES ($1, NULL, 'done', $2)""",
                    deal_id, command.actor_user_id,
                )
                for account, delta, balance, other in (
                    (debit, -amount, from_balance, recipient["name"]),
                    (credit, amount, to_balance, sender["name"]),
                ):
                    await connection.execute(
                        "UPDATE client_accounts SET balance=$2 WHERE id=$1",
                        account["id"], balance,
                    )
                    await connection.execute(
                        """INSERT INTO transactions
                             (client_id, account_id, amount, balance_after, comment,
                              source, idempotency_key)
                           VALUES ($1, $2, $3, $4, $5, 'client_transfer', $6)""",
                        account["client_id"],
                        account["id"],
                        delta,
                        balance,
                        f"Перевод #{deal_id}: {other}" + (
                            f" | {command.comment}" if command.comment else ""
                        ),
                        f"client_transfer:{deal_id}",
                    )
                result = ClientTransferResult(
                    deal_id=int(deal_id),
                    from_client_name=sender["name"],
                    to_client_name=recipient["name"],
                    to_chat_id=int(recipient["chat_id"]),
                    from_balance=from_balance,
                    to_balance=to_balance,
                    amount=amount,
                    currency=command.currency.upper(),
                    precision=precision,
                    created_at=deal["created_at"],
                    repeated=False,
                )
        return result

    @staticmethod
    def _validate(command: ClientTransferCommand) -> Decimal:
        try:
            amount = Decimal(str(command.amount))
        except (InvalidOperation, ValueError, TypeError):
            raise DomainValidationError("Некорректная сумма перевода") from None
        if not amount.is_finite() or amount <= 0:
            raise DomainValidationError("Сумма перевода должна быть больше нуля")
        if not command.currency or not command.currency.isalnum():
            raise DomainValidationError("Некорректная валюта перевода")
        if command.source not in {"crm", "tg_bot"} or not command.source_ref.strip():
            raise DomainValidationError("Не указан источник перевода")
        if (command.from_chat_id is None) == (command.from_client_id is None):
            raise DomainValidationError("Укажите одного отправителя")
        if (command.to_client_name is None) == (command.to_client_id is None):
            raise DomainValidationError("Укажите одного получателя")
        return amount

    async def reject(self, command: ClientTransferCommand) -> bool:
        amount = self._validate(command)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"client-transfer:{command.source}:{command.source_ref}",
                )
                existing = await connection.fetchrow(
                    """SELECT id, status FROM deals
                       WHERE source=$1 AND source_kind='client_transfer' AND source_ref=$2""",
                    command.source, command.source_ref,
                )
                if existing is not None:
                    return existing["status"] == "canceled"
                sender = await self._client(
                    connection, client_id=command.from_client_id, chat_id=command.from_chat_id
                )
                recipient = await self._client(
                    connection, client_id=command.to_client_id, name=command.to_client_name
                )
                body = {
                    "from_client_id": sender["id"], "to_client_id": recipient["id"],
                    "from_client_name": sender["name"], "to_client_name": recipient["name"],
                    "currency": command.currency.upper(), "amount": str(amount),
                    "actor_tg_user_id": command.actor_tg_user_id,
                    "rejected_without_posting": True,
                }
                deal_id = await connection.fetchval(
                    """INSERT INTO deals
                         (deal_type, city, client_id, status, created_by, source,
                          source_kind, source_ref, comment, body)
                       VALUES ('client_transfer', $1, $2, 'canceled', $3, $4,
                               'client_transfer', $5, $6, $7::jsonb)
                       RETURNING id""",
                    command.city, sender["id"], command.actor_user_id, command.source,
                    command.source_ref, command.comment, json.dumps(body, ensure_ascii=False),
                )
                await connection.execute(
                    """INSERT INTO deal_status_events
                         (deal_id, old_status, new_status, actor_user_id)
                       VALUES ($1, NULL, 'canceled', $2)""",
                    deal_id, command.actor_user_id,
                )
        return True

    async def adjust(
        self, command: ClientTransferAdjustmentCommand
    ) -> ClientTransferAdjustmentResult:
        if command.deal_id <= 0 or not command.source_ref.strip():
            raise DomainValidationError("Некорректная операция перевода")
        new_amount = (
            Decimal("0") if command.new_amount is None
            else self._validate_adjustment_amount(command.new_amount)
        )
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                deal = await connection.fetchrow(
                    """SELECT id, status, source, source_kind, body
                       FROM deals WHERE id=$1 FOR UPDATE""",
                    command.deal_id,
                )
                if deal is None or deal["source"] != "tg_bot" or deal["source_kind"] != "client_transfer":
                    raise DomainValidationError("Чек перевода не найден")
                body = deal["body"]
                if isinstance(body, str):
                    body = json.loads(body)
                sender_chat_id = await connection.fetchval(
                    "SELECT chat_id FROM clients WHERE id=$1", body["from_client_id"]
                )
                if sender_chat_id != command.from_chat_id:
                    raise DomainValidationError("Изменить перевод можно только в чате отправителя")

                prior = await connection.fetchrow(
                    """SELECT deal_id, old_amount, new_amount
                       FROM client_transfer_adjustments WHERE source_ref=$1""",
                    command.source_ref,
                )
                if prior is not None:
                    if prior["deal_id"] != command.deal_id or prior["new_amount"] != new_amount:
                        raise DomainStateError("Эта команда уже использована для другой корректировки")
                    repeated = True
                else:
                    repeated = False
                    if deal["status"] != "done":
                        raise DomainStateError("Перевод уже отменён")

                accounts = await connection.fetch(
                    """SELECT id, client_id, precision, balance FROM client_accounts
                       WHERE client_id=ANY($1::bigint[]) AND currency_code=$2 AND is_active
                       ORDER BY id FOR UPDATE""",
                    [body["from_client_id"], body["to_client_id"]], body["currency"],
                )
                by_client = {row["client_id"]: row for row in accounts}
                if len(by_client) != 2:
                    raise DomainStateError("Счёт отправителя или получателя недоступен")
                debit = by_client[body["from_client_id"]]
                credit = by_client[body["to_client_id"]]
                precision = int(debit["precision"])
                if credit["precision"] != precision:
                    raise DomainStateError("Точность счетов не совпадает")
                quantum = Decimal(1).scaleb(-precision)
                if new_amount != new_amount.quantize(quantum):
                    raise DomainValidationError(
                        f"Для {body['currency']} допускается не более {precision} знаков после запятой"
                    )
                if repeated:
                    old_amount = Decimal(str(prior["old_amount"]))
                else:
                    old_amount = Decimal(str(body["amount"]))
                delta = new_amount - old_amount
                from_balance = Decimal(str(debit["balance"])) - (delta if not repeated else 0)
                to_balance = Decimal(str(credit["balance"])) + (delta if not repeated else 0)
                if (
                    not repeated and delta != 0 and not command.allow_negative
                    and (from_balance < 0 or to_balance < 0)
                ):
                    raise DomainStateError("Недостаточно средств для корректировки перевода")

                if not repeated and delta != 0:
                    adjustment_id = await connection.fetchval(
                        """INSERT INTO client_transfer_adjustments
                             (deal_id, source_ref, old_amount, new_amount, actor_tg_user_id)
                           VALUES ($1, $2, $3, $4, $5) RETURNING id""",
                        command.deal_id, command.source_ref, old_amount, new_amount,
                        command.actor_tg_user_id,
                    )
                    for account, amount, balance in (
                        (debit, -delta, from_balance),
                        (credit, delta, to_balance),
                    ):
                        await connection.execute(
                            "UPDATE client_accounts SET balance=$2 WHERE id=$1",
                            account["id"], balance,
                        )
                        await connection.execute(
                            """INSERT INTO transactions
                                 (client_id, account_id, amount, balance_after, comment,
                                  source, idempotency_key)
                               VALUES ($1, $2, $3, $4, $5, 'client_transfer_adjustment', $6)""",
                            account["client_id"], account["id"], amount, balance,
                            f"Корректировка перевода #{command.deal_id}: {old_amount} → {new_amount}",
                            f"client_transfer_adjustment:{adjustment_id}",
                        )
                    body["amount"] = str(new_amount)
                    await connection.execute(
                        "UPDATE deals SET body=$2::jsonb, status=$3, updated_at=NOW() WHERE id=$1",
                        command.deal_id, json.dumps(body, ensure_ascii=False),
                        "canceled" if new_amount == 0 else "done",
                    )
                    if new_amount == 0:
                        await connection.execute(
                            """INSERT INTO deal_status_events
                                 (deal_id, old_status, new_status, payload)
                               VALUES ($1, 'done', 'canceled', $2::jsonb)""",
                            command.deal_id, json.dumps({"adjustment_id": adjustment_id}),
                        )
                elif not repeated:
                    # Same amount is a no-op: the command must not create an audit row.
                    repeated = True

                to_chat_id = await connection.fetchval(
                    "SELECT chat_id FROM clients WHERE id=$1", body["to_client_id"]
                )
                return ClientTransferAdjustmentResult(
                    deal_id=command.deal_id,
                    from_client_name=body["from_client_name"],
                    to_client_name=body["to_client_name"],
                    to_chat_id=int(to_chat_id),
                    from_balance=from_balance,
                    to_balance=to_balance,
                    old_amount=old_amount,
                    new_amount=new_amount,
                    currency=body["currency"],
                    precision=precision,
                    canceled=new_amount == 0,
                    repeated=repeated,
                )

    @staticmethod
    def _validate_adjustment_amount(value: Decimal) -> Decimal:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            raise DomainValidationError("Некорректная сумма перевода") from None
        if not amount.is_finite() or amount <= 0:
            raise DomainValidationError("Сумма перевода должна быть больше нуля")
        return amount

    @staticmethod
    async def _client(
        connection: asyncpg.Connection,
        *,
        client_id: int | None = None,
        chat_id: int | None = None,
        name: str | None = None,
    ) -> asyncpg.Record:
        if name is not None:
            rows = await connection.fetch(
                """SELECT id, chat_id, name FROM clients
                   WHERE is_active AND LOWER(BTRIM(name))=LOWER(BTRIM($1))
                   LIMIT 2""",
                name,
            )
            if len(rows) > 1:
                raise DomainValidationError(
                    "Найдено несколько клиентов с таким именем. Уточните название чата"
                )
            row = rows[0] if rows else None
        elif chat_id is not None:
            row = await connection.fetchrow(
                "SELECT id, chat_id, name FROM clients WHERE is_active AND chat_id=$1", chat_id
            )
        else:
            row = await connection.fetchrow(
                "SELECT id, chat_id, name FROM clients WHERE is_active AND id=$1", client_id
            )
        if row is None:
            raise DomainValidationError("Клиент не найден")
        return row

    @staticmethod
    async def _repeated(
        connection: asyncpg.Connection,
        existing: asyncpg.Record,
        command: ClientTransferCommand,
        amount: Decimal,
    ) -> ClientTransferResult:
        body = existing["body"]
        if existing["status"] == "canceled":
            raise DomainStateError("Этот перевод уже отклонён")
        if isinstance(body, str):
            body = json.loads(body)
        if body["currency"] != command.currency.upper() or Decimal(body["amount"]) != amount:
            raise DomainStateError("Ключ перевода уже использован для другой операции")
        if existing["comment"] != command.comment:
            raise DomainStateError("Ключ перевода уже использован с другим комментарием")
        if command.to_client_id is not None and body["to_client_id"] != command.to_client_id:
            raise DomainStateError("Ключ перевода уже использован для другого получателя")
        if command.from_client_id is not None and body["from_client_id"] != command.from_client_id:
            raise DomainStateError("Ключ перевода уже использован для другого отправителя")
        if command.from_chat_id is not None:
            current_sender = await connection.fetchval(
                "SELECT id FROM clients WHERE chat_id=$1", command.from_chat_id
            )
            if body["from_client_id"] != current_sender:
                raise DomainStateError("Ключ перевода уже использован в другом чате")
        rows = await connection.fetch(
            """SELECT t.client_id, t.balance_after, a.precision
               FROM transactions t JOIN client_accounts a ON a.id=t.account_id
               WHERE t.source='client_transfer' AND t.idempotency_key=$1""",
            f"client_transfer:{existing['id']}",
        )
        by_client = {row["client_id"]: row for row in rows}
        if body["from_client_id"] not in by_client or body["to_client_id"] not in by_client:
            raise DomainStateError("У повторного перевода не найдены обе проводки")
        return ClientTransferResult(
            deal_id=int(existing["id"]),
            from_client_name=body["from_client_name"],
            to_client_name=body["to_client_name"],
            to_chat_id=int(await connection.fetchval(
                "SELECT chat_id FROM clients WHERE id=$1", body["to_client_id"]
            )),
            from_balance=Decimal(str(by_client[body["from_client_id"]]["balance_after"])),
            to_balance=Decimal(str(by_client[body["to_client_id"]]["balance_after"])),
            amount=amount,
            currency=body["currency"],
            precision=int(by_client[body["from_client_id"]]["precision"]),
            created_at=existing["created_at"],
            repeated=True,
        )
