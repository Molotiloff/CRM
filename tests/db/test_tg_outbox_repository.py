from __future__ import annotations

import json

import pytest

from db_asyncpg.repositories.tg_outbox import TgOutboxRepository


@pytest.mark.asyncio
async def test_outbox_claim_is_exclusive_and_can_be_marked_sent(pool) -> None:
    async with pool.acquire() as con:
        outbox_id = await con.fetchval(
            """
            INSERT INTO tg_outbox (kind, payload, dedup_key)
            VALUES ('deal_status_changed', $1::jsonb, 'test:sent')
            RETURNING id
            """,
            json.dumps({"dealId": 1, "newStatus": "fixed"}),
        )
    repository = TgOutboxRepository(pool)

    assert await repository.count_pending() == 1
    claimed = await repository.claim_batch()
    assert await repository.count_pending() == 1
    claimed_again = await repository.claim_batch()
    await repository.mark_sent(outbox_id)
    assert await repository.count_pending() == 0

    assert [item.id for item in claimed] == [outbox_id]
    assert claimed[0].attempts == 1
    assert claimed_again == []
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT status, attempts, sent_at FROM tg_outbox WHERE id = $1", outbox_id
        )
    assert row["status"] == "sent"
    assert row["attempts"] == 1
    assert row["sent_at"] is not None


@pytest.mark.asyncio
async def test_outbox_failure_is_retried_then_terminal(pool) -> None:
    async with pool.acquire() as con:
        outbox_id = await con.fetchval(
            """
            INSERT INTO tg_outbox (kind, payload, dedup_key)
            VALUES ('deal_status_changed', '{}'::jsonb, 'test:failed')
            RETURNING id
            """
        )
    repository = TgOutboxRepository(pool)
    item = (await repository.claim_batch(max_attempts=2))[0]
    await repository.mark_failed(
        item.id,
        error="temporary",
        retry=True,
        retry_delay_seconds=0,
    )
    retried = (await repository.claim_batch(max_attempts=2))[0]
    await repository.mark_failed(
        retried.id,
        error="terminal",
        retry=False,
        retry_delay_seconds=0,
    )

    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT status, attempts, last_error FROM tg_outbox WHERE id = $1", outbox_id
        )
    assert row["status"] == "failed"
    assert row["attempts"] == 2
    assert row["last_error"] == "terminal"
