from dataclasses import replace
from decimal import Decimal

import pytest

from db_asyncpg.repositories.client_transfers import ClientTransferRepository
from db_asyncpg.repositories.deals import DealRepository
from domain import DomainStateError
from services.crm.client_transfer_models import ClientTransferAdjustmentCommand
from services.crm.client_transfer_service import ClientTransferService
from services.crm.client_transfer_workflow import ClientTransferWorkflow
from services.crm.deal_events import DealEventBus
from services.crm.deal_service import DealValidationError
from services.crm.deal_source_commands import ExchangeSourceEdit
from tests.db.conftest import balance_of
from tests.db.test_client_transfer_service import _clients, _command
from tests.db.test_deal_settlement_service import _deal_and_watch, _service, _transfer
from tests.db.test_deal_source_mutation_uow import _seed_exchange, _source_mutation_service
from tests.fakes import FakeMessenger


@pytest.mark.parametrize("flags", [True, False])
async def test_crm_exchange_edit_preserves_original_ledger_direction(pool, repo, client_id, flags):
    _, deal = await _seed_exchange(pool, repo, client_id, suffix="MGMT1", source="crm")
    if not flags:
        await repo.withdraw(client_id=client_id, currency_code="USDT", amount=Decimal("200"))
        await repo.deposit(client_id=client_id, currency_code="RUB", amount=Decimal("18000"))
        async with pool.acquire() as con:
            await con.execute("UPDATE deals SET body=body || $2::jsonb WHERE id=$1", deal.id,
                              '{"recv_is_deposit":false,"pay_is_withdraw":false}')
    updated = await _source_mutation_service(pool, DealEventBus()).edit_exchange(
        deal.id, ExchangeSourceEdit(operation_id=789, recv_code="USDT", recv_amount=Decimal("150"),
            pay_code="RUB", pay_amount=Decimal("13500"), rate=Decimal("90"), note="CRM edit"),
        actor_name="Manager",
    )
    assert await balance_of(repo, client_id, "USDT") == (Decimal("150") if flags else Decimal("-150"))
    assert await balance_of(repo, client_id, "RUB") == (Decimal("-13500") if flags else Decimal("13500"))
    assert updated.body.get("recv_is_deposit") is flags


@pytest.mark.parametrize("status", ["done", "canceled"])
async def test_crm_exchange_terminal_edit_remains_forbidden(pool, repo, client_id, status):
    _, deal = await _seed_exchange(pool, repo, client_id, suffix="MGMT1", source="crm")
    async with pool.acquire() as con:
        await con.execute("UPDATE deals SET status=$2 WHERE id=$1", deal.id, status)
    with pytest.raises(DealValidationError):
        await _source_mutation_service(pool, DealEventBus()).edit_exchange(
            deal.id, ExchangeSourceEdit(operation_id=789, recv_code="USDT", recv_amount=Decimal("150"),
                pay_code="RUB", pay_amount=Decimal("13500"), rate=Decimal("90")), actor_name="Manager",
        )
    assert await balance_of(repo, client_id, "USDT") == 100


async def test_conversion_table_completion_is_idempotent(pool, repo, client_id):
    req_id, deal = await _seed_exchange(pool, repo, client_id, suffix="MGMT1", source="crm")
    async with pool.acquire() as con:
        await con.execute("UPDATE deals SET deal_type='conversion' WHERE id=$1", deal.id)
        table_id = await con.fetchval("UPDATE exchange_request_links SET is_table_done=TRUE WHERE client_req_id=$1 RETURNING table_req_id", req_id)
    repository = DealRepository(pool)
    first = await repository.complete_exchange_after_table(str(table_id), actor_user_id=None)
    second = await repository.complete_exchange_after_table(str(table_id), actor_user_id=None)
    assert str(first.status) == str(second.status) == "done"
    async with pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM deal_status_events WHERE deal_id=$1 AND new_status='done'", deal.id) == 1


async def test_transfer_ui_adjustment_rejects_stale_amount_and_retry_is_idempotent(pool):
    _, recipient = await _clients(pool)
    service = ClientTransferService(ClientTransferRepository(pool), DealEventBus())
    transfer = await service.transfer(_command(recipient_id=recipient))
    command = ClientTransferAdjustmentCommand(
        deal_id=transfer.deal_id, from_chat_id=-700001, source_ref="crm:1:adjust",
        new_amount=Decimal("30"), expected_amount=Decimal("25"),
    )
    messenger = FakeMessenger()
    workflow = ClientTransferWorkflow(service, messenger)
    first = await workflow.adjust(command)
    assert first.from_balance == 70
    assert len(messenger.sent_to(-700001)) == len(messenger.sent_to(-700002)) == 2
    second = await workflow.adjust(command)
    assert second.repeated
    assert len(messenger.sent_to(-700001)) == 2
    with pytest.raises(DomainStateError, match="Сумма перевода изменилась"):
        await workflow.adjust(replace(command, source_ref="crm:1:stale", new_amount=Decimal("40")))
    canceled = await workflow.adjust(replace(command, source_ref="crm:1:cancel", new_amount=None, expected_amount=Decimal("30")))
    assert canceled.from_balance == 100
    assert canceled.to_balance == 0
    assert canceled.canceled


async def test_settlement_read_includes_actual_and_evidence_without_writes(pool, repo, payment_watch_repo, client_id):
    await repo.deposit(client_id=client_id, currency_code="USDT", amount=Decimal("100"))
    deal_id, watch_id = await _deal_and_watch(pool, payment_watch_repo, client_id)
    service = _service(pool)
    result = await service.settle(_transfer(watch_id, Decimal("90"), "tx-management"))
    items = await service.list_for_deal(deal_id)
    assert len(items) == 1
    assert items[0].settlement_id == result.settlement_id
    assert (items[0].expected, items[0].actual, items[0].delta) == (Decimal("100"), Decimal("90"), Decimal("-10"))
    assert items[0].evidence.tx_hash == "tx-management"
    assert str(items[0].status) == "needs_review"
