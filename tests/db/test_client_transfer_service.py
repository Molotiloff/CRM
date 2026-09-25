from __future__ import annotations

from decimal import Decimal

import pytest

from db_asyncpg.repositories.client_transfers import ClientTransferRepository
from db_asyncpg.repositories.clients import ClientsRepo
from domain import DomainStateError, DomainValidationError
from services.crm.client_transfer_service import ClientTransferCommand, ClientTransferService
from services.crm.deal_events import DealEventBus


async def _clients(pool, *, duplicate_name: bool = False) -> tuple[int, int]:
    repo = ClientsRepo(pool)
    sender = await repo.ensure_client(-700001, "Отправитель")
    recipient = await repo.ensure_client(-700002, "Получатель")
    for client_id in (sender, recipient):
        await repo.add_currency(client_id, "RUB", precision=2)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE client_accounts SET balance=100 WHERE client_id=$1 AND currency_code='RUB'",
            sender,
        )
    if duplicate_name:
        async with pool.acquire() as connection:
            other = await connection.fetchval(
                "INSERT INTO clients(chat_id, name) VALUES (-700003, 'Получатель') RETURNING id"
            )
        await repo.add_currency(other, "RUB", precision=2)
    return sender, recipient


def _command(*, recipient_id: int, amount: str = "25", allow_negative: bool = False):
    return ClientTransferCommand(
        from_chat_id=-700001,
        to_client_id=recipient_id,
        amount=Decimal(amount),
        currency="RUB",
        source="tg_bot",
        source_ref="-700001:123",
        city="екб",
        allow_negative=allow_negative,
    )


@pytest.mark.asyncio
async def test_client_transfer_posts_both_legs_and_deal_once(pool) -> None:
    sender, recipient = await _clients(pool)
    service = ClientTransferService(ClientTransferRepository(pool), DealEventBus())

    first = await service.transfer(_command(recipient_id=recipient))
    repeated = await service.transfer(_command(recipient_id=recipient))

    assert first.repeated is False
    assert repeated.repeated is True
    assert repeated.deal_id == first.deal_id
    assert repeated.created_at == first.created_at
    assert first.from_balance == Decimal("75")
    assert first.to_balance == Decimal("25")
    async with pool.acquire() as connection:
        accounts = await connection.fetch(
            "SELECT client_id, balance FROM client_accounts WHERE currency_code='RUB' ORDER BY client_id"
        )
        legs = await connection.fetch(
            "SELECT client_id, amount, balance_after FROM transactions ORDER BY id"
        )
        deal = await connection.fetchrow(
            "SELECT deal_type, status, source_kind, client_id FROM deals WHERE id=$1",
            first.deal_id,
        )
    assert [(row["client_id"], row["balance"]) for row in accounts] == [
        (sender, Decimal("75")), (recipient, Decimal("25"))
    ]
    assert [(row["client_id"], row["amount"]) for row in legs] == [
        (sender, Decimal("-25")), (recipient, Decimal("25"))
    ]
    assert dict(deal) == {
        "deal_type": "client_transfer", "status": "done",
        "source_kind": "client_transfer", "client_id": sender,
    }


@pytest.mark.asyncio
async def test_client_transfer_requires_confirmation_for_negative_balance(pool) -> None:
    _, recipient = await _clients(pool)
    service = ClientTransferService(ClientTransferRepository(pool), DealEventBus())

    with pytest.raises(DomainStateError, match="Недостаточно средств"):
        await service.transfer(_command(recipient_id=recipient, amount="125"))

    result = await service.transfer(
        _command(recipient_id=recipient, amount="125", allow_negative=True)
    )
    assert result.from_balance == Decimal("-25")
    assert result.to_balance == Decimal("125")


@pytest.mark.asyncio
async def test_client_transfer_rejects_ambiguous_name_and_fraction(pool) -> None:
    await _clients(pool, duplicate_name=True)
    service = ClientTransferService(ClientTransferRepository(pool), DealEventBus())
    recipients = await service.find_recipients("получатель")
    assert len(recipients) == 2
    with pytest.raises(DomainValidationError, match="несколько клиентов"):
        await service.transfer(
            ClientTransferCommand(
                from_chat_id=-700001, to_client_name="Получатель",
                amount=Decimal("1"), currency="RUB", source="tg_bot",
                source_ref="duplicate", city="екб",
            )
        )
    with pytest.raises(DomainValidationError, match="знаков после запятой"):
        await service.transfer(
            ClientTransferCommand(
                from_chat_id=-700001, to_client_id=recipients[0]["id"],
                amount=Decimal("1.001"), currency="RUB", source="tg_bot",
                source_ref="fraction", city="екб",
            )
        )


@pytest.mark.asyncio
async def test_client_transfer_does_not_move_cash_scoped_rub(pool) -> None:
    sender, recipient = await _clients(pool)
    async with pool.acquire() as connection:
        await connection.execute(
            """INSERT INTO cash_chat_registry
                 (chat_id, client_id, city, location_name, cash_currency_codes)
               VALUES (-700002, $1, 'мск', 'Поэты', ARRAY['RUB'])""",
            recipient,
        )
    service = ClientTransferService(ClientTransferRepository(pool), DealEventBus())
    with pytest.raises(DomainValidationError, match="учитывается как касса"):
        await service.transfer(_command(recipient_id=recipient))
    async with pool.acquire() as connection:
        balance = await connection.fetchval(
            "SELECT balance FROM client_accounts WHERE client_id=$1 AND currency_code='RUB'",
            sender,
        )
    assert balance == Decimal("100")


@pytest.mark.asyncio
async def test_rejected_transfer_cannot_be_confirmed_later(pool) -> None:
    sender, recipient = await _clients(pool)
    service = ClientTransferService(ClientTransferRepository(pool), DealEventBus())

    assert await service.reject(_command(recipient_id=recipient, amount="125"))
    assert await service.reject(_command(recipient_id=recipient, amount="125"))
    with pytest.raises(DomainStateError, match="уже отклонён"):
        await service.transfer(
            _command(recipient_id=recipient, amount="125", allow_negative=True)
        )
    async with pool.acquire() as connection:
        balance = await connection.fetchval(
            "SELECT balance FROM client_accounts WHERE client_id=$1 AND currency_code='RUB'",
            sender,
        )
        txn_count = await connection.fetchval("SELECT COUNT(*) FROM transactions")
        deal_status = await connection.fetchval(
            "SELECT status FROM deals WHERE source_kind='client_transfer'"
        )
    assert balance == Decimal("100")
    assert txn_count == 0
    assert deal_status == "canceled"
