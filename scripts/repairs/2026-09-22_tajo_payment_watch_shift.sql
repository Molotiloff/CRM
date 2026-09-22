\set ON_ERROR_STOP on

BEGIN;

-- Repair a one-item fulfillment queue shift for SKYEX | TAJO ЧЛБ.
--
-- Actual blockchain evidence:
--   event 1323 / 2928 USDT -> deal 597 / queue 4
--   event 1324 / 7941 USDT -> deal 600 / queue 5
-- Queue 1 / deal 584 / 4137 USDT was never sent and must be canceled.
--
-- Client ledger movements are intentionally left append-only and unchanged.
-- Managers already neutralized the affected USDT balance with transactions
-- 18500 and 18526. Reversing the settlement deltas now would change the
-- correct resulting client balance of 0.15 USDT.

DO $$
DECLARE
    repair_marker CONSTANT TEXT := 'repair:2026-09-22:tajo-payment-watch-shift';
    first_tx_hash CONSTANT TEXT :=
        '5df03292b5d4179dd4043aa0ad08ffc0cd037bdefe6aea6aec7498d96d73300b';
    second_tx_hash CONSTANT TEXT :=
        'cd370ac7e698a4fdc11a506183ba8c66ce57b1ec9427eb437d80e33ab9e045d0';
BEGIN
    -- A repeated run is a no-op only when the complete target state exists.
    IF EXISTS (
        SELECT 1
        FROM usdt_fulfillment_queue
        WHERE id = 1
          AND deal_id = 584
          AND status = 'canceled'
          AND payment_watch_id IS NULL
    ) AND EXISTS (
        SELECT 1
        FROM usdt_fulfillment_queue
        WHERE id = 4
          AND deal_id = 597
          AND status = 'completed'
          AND payment_watch_id = 1376
          AND payment_event_id = 1323
    ) AND EXISTS (
        SELECT 1
        FROM usdt_fulfillment_queue
        WHERE id = 5
          AND deal_id = 600
          AND status = 'completed'
          AND payment_watch_id = 1377
          AND payment_event_id = 1324
    ) AND EXISTS (
        SELECT 1
        FROM deal_settlements
        WHERE id = 1
          AND deal_id = 597
          AND payment_event_id = 1323
          AND expected_qty = 2928
          AND actual_qty = 2928
          AND review_status = 'matched'
    ) AND EXISTS (
        SELECT 1
        FROM deal_settlements
        WHERE id = 2
          AND deal_id = 600
          AND payment_event_id = 1324
          AND expected_qty = 7941
          AND actual_qty = 7941
          AND review_status = 'matched'
    ) THEN
        RAISE NOTICE 'TAJO payment-watch shift is already repaired';
        RETURN;
    END IF;

    -- Lock every mutable row before checking the expected broken state.
    PERFORM id
    FROM usdt_fulfillment_queue
    WHERE id IN (1, 4, 5)
    ORDER BY id
    FOR UPDATE;

    PERFORM id
    FROM payment_watches
    WHERE id IN (1376, 1377)
    ORDER BY id
    FOR UPDATE;

    PERFORM id
    FROM payment_watch_events
    WHERE id IN (1323, 1324)
    ORDER BY id
    FOR UPDATE;

    PERFORM id
    FROM deal_settlements
    WHERE id IN (1, 2)
    ORDER BY id
    FOR UPDATE;

    PERFORM id
    FROM deals
    WHERE id IN (584, 597, 600)
    ORDER BY id
    FOR UPDATE;

    IF (SELECT COUNT(*) FROM usdt_fulfillment_queue WHERE id IN (1, 4, 5)) <> 3
       OR (SELECT COUNT(*) FROM payment_watches WHERE id IN (1376, 1377)) <> 2
       OR (SELECT COUNT(*) FROM payment_watch_events WHERE id IN (1323, 1324)) <> 2
       OR (SELECT COUNT(*) FROM deal_settlements WHERE id IN (1, 2)) <> 2
       OR (SELECT COUNT(*) FROM deals WHERE id IN (584, 597, 600)) <> 3 THEN
        RAISE EXCEPTION 'TAJO repair targets are incomplete';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM clients
        WHERE chat_id = -5013630397
          AND id = (SELECT client_id FROM deals WHERE id = 584)
          AND id = (SELECT client_id FROM deals WHERE id = 597)
          AND id = (SELECT client_id FROM deals WHERE id = 600)
    ) THEN
        RAISE EXCEPTION 'TAJO repair deals do not belong to chat -5013630397';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM usdt_fulfillment_queue
        WHERE id = 1 AND deal_id = 584 AND request_kind = 'sale'
          AND qty = 4137 AND sequence_no = 1024 AND status = 'executing'
          AND payment_watch_id = 1376 AND payment_event_id IS NULL
    ) OR NOT EXISTS (
        SELECT 1 FROM usdt_fulfillment_queue
        WHERE id = 4 AND deal_id = 597 AND request_kind = 'sale'
          AND qty = 2928 AND sequence_no = 3072 AND status = 'executing'
          AND payment_watch_id = 1377 AND payment_event_id IS NULL
    ) OR NOT EXISTS (
        SELECT 1 FROM usdt_fulfillment_queue
        WHERE id = 5 AND deal_id = 600 AND request_kind = 'sale'
          AND qty = 7941 AND sequence_no = 4096 AND status = 'queued'
          AND payment_watch_id IS NULL AND payment_event_id IS NULL
    ) THEN
        RAISE EXCEPTION 'TAJO fulfillment queue differs from the verified preflight';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM payment_watch_events event
        JOIN payment_watches watch ON watch.id = event.watch_id
        WHERE event.id = 1323
          AND event.watch_id = 1376
          AND event.event_type = 'MAIN'
          AND event.direction = 'IN'
          AND event.amount = 2928
          AND event.tx_hash = first_tx_hash
          AND watch.deal_id = 584
          AND watch.chat_id = -5013630397
          AND watch.status = 'COMPLETED'
    ) OR NOT EXISTS (
        SELECT 1
        FROM payment_watch_events event
        JOIN payment_watches watch ON watch.id = event.watch_id
        WHERE event.id = 1324
          AND event.watch_id = 1377
          AND event.event_type = 'MAIN'
          AND event.direction = 'IN'
          AND event.amount = 7941
          AND event.tx_hash = second_tx_hash
          AND watch.deal_id = 597
          AND watch.chat_id = -5013630397
          AND watch.status = 'COMPLETED'
    ) THEN
        RAISE EXCEPTION 'TAJO blockchain evidence differs from the verified preflight';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM deal_settlements
        WHERE id = 1 AND deal_id = 584 AND payment_event_id = 1323
          AND expected_qty = 4137 AND actual_qty = 2928
          AND review_status = 'needs_review' AND resolution IS NULL
    ) OR NOT EXISTS (
        SELECT 1 FROM deal_settlements
        WHERE id = 2 AND deal_id = 597 AND payment_event_id = 1324
          AND expected_qty = 2928 AND actual_qty = 7941
          AND review_status = 'needs_review' AND resolution IS NULL
    ) THEN
        RAISE EXCEPTION 'TAJO settlements differ from the verified preflight';
    END IF;

    -- The incorrect deltas and the manager compensations are retained as an
    -- append-only audit pair. Their verified combination leaves 0.15 USDT.
    IF NOT EXISTS (
        SELECT 1 FROM transactions
        WHERE id = 18499 AND client_id = (SELECT client_id FROM deals WHERE id = 584)
          AND amount = 1209 AND balance_after = 4137.15
          AND idempotency_key = 'settlement:1323:actual_delta'
    ) OR NOT EXISTS (
        SELECT 1 FROM transactions
        WHERE id = 18500 AND client_id = (SELECT client_id FROM deals WHERE id = 584)
          AND amount = -4137 AND balance_after = 0.15
          AND source = 'command'
    ) OR NOT EXISTS (
        SELECT 1 FROM transactions
        WHERE id = 18507 AND client_id = (SELECT client_id FROM deals WHERE id = 584)
          AND amount = -5013 AND balance_after = 2928.15
          AND idempotency_key = 'settlement:1324:actual_delta'
    ) OR NOT EXISTS (
        SELECT 1 FROM transactions
        WHERE id = 18526 AND client_id = (SELECT client_id FROM deals WHERE id = 584)
          AND amount = -2928 AND balance_after = 0.15
          AND source = 'command'
    ) THEN
        RAISE EXCEPTION 'TAJO client ledger differs from the verified preflight';
    END IF;

    IF EXISTS (
        SELECT 1 FROM firm_position_moves
        WHERE deal_id IN (584, 597, 600)
           OR idempotency_key IN (
               'fulfillment:1:position_sale',
               'fulfillment:4:position_sale',
               'fulfillment:5:position_sale'
           )
    ) OR EXISTS (
        SELECT 1 FROM firm_wallet_fact_snapshots
        WHERE idempotency_key IN (
            'fulfillment:1:wallet_fact',
            'fulfillment:4:wallet_fact',
            'fulfillment:5:wallet_fact'
        )
    ) THEN
        RAISE EXCEPTION 'TAJO repair found unexpected position or wallet movements';
    END IF;

    -- Release unique watch links before assigning them to the proper items.
    UPDATE usdt_fulfillment_queue
    SET payment_watch_id = NULL
    WHERE id IN (1, 4);

    UPDATE payment_watches
    SET deal_id = CASE id
        WHEN 1376 THEN 597
        WHEN 1377 THEN 600
    END
    WHERE id IN (1376, 1377);

    UPDATE deal_settlements
    SET deal_id = 597,
        expected_qty = 2928,
        actual_qty = 2928,
        review_status = 'matched',
        resolution = NULL,
        resolved_by = NULL,
        resolved_at = NULL,
        comment = repair_marker ||
            '; manager ledger corrections 18499/18500 retained',
        updated_at = NOW()
    WHERE id = 1;

    UPDATE deal_settlements
    SET deal_id = 600,
        expected_qty = 7941,
        actual_qty = 7941,
        review_status = 'matched',
        resolution = NULL,
        resolved_by = NULL,
        resolved_at = NULL,
        comment = repair_marker ||
            '; manager ledger corrections 18507/18526 retained',
        updated_at = NOW()
    WHERE id = 2;

    UPDATE usdt_fulfillment_queue
    SET status = 'canceled',
        payment_watch_id = NULL,
        payment_event_id = NULL,
        position_move_id = NULL,
        completed_by = NULL,
        wallet_source = NULL,
        completed_at = NULL,
        cancel_reason = repair_marker || '; transfer 4137 USDT was not observed'
    WHERE id = 1;

    UPDATE usdt_fulfillment_queue
    SET status = 'completed',
        payment_watch_id = 1376,
        payment_event_id = 1323,
        position_move_id = NULL,
        completed_by = NULL,
        wallet_source = 'firm_wallet',
        completed_at = (
            SELECT block_ts FROM payment_watch_events WHERE id = 1323
        ),
        cancel_reason = NULL
    WHERE id = 4;

    UPDATE usdt_fulfillment_queue
    SET status = 'completed',
        payment_watch_id = 1377,
        payment_event_id = 1324,
        position_move_id = NULL,
        completed_by = NULL,
        wallet_source = 'firm_wallet',
        completed_at = (
            SELECT block_ts FROM payment_watch_events WHERE id = 1324
        ),
        cancel_reason = NULL
    WHERE id = 5;

    UPDATE deals
    SET status = 'done',
        tronscan_url = 'https://tronscan.org/#/transaction/' || first_tx_hash,
        body = body || jsonb_build_object(
            'payment_tx_hash', first_tx_hash,
            'payment_event_id', 1323,
            'payment_watch_id', 1376,
            'payment_repair', repair_marker
        )
    WHERE id = 597 AND status = 'new';

    UPDATE deals
    SET status = 'done',
        tronscan_url = 'https://tronscan.org/#/transaction/' || second_tx_hash,
        body = body || jsonb_build_object(
            'payment_tx_hash', second_tx_hash,
            'payment_event_id', 1324,
            'payment_watch_id', 1377,
            'payment_repair', repair_marker
        )
    WHERE id = 600 AND status = 'new';

    IF (SELECT status FROM deals WHERE id = 597) <> 'done'
       OR (SELECT status FROM deals WHERE id = 600) <> 'done' THEN
        RAISE EXCEPTION 'TAJO repair could not move target deals to done';
    END IF;

    INSERT INTO deal_status_events(
        deal_id,
        old_status,
        new_status,
        actor_user_id,
        payload
    )
    SELECT
        target.deal_id,
        'new',
        'done',
        NULL,
        jsonb_build_object(
            'repair', repair_marker,
            'paymentWatchId', target.watch_id,
            'paymentEventId', target.event_id,
            'txHash', target.tx_hash,
            'paymentAmount', target.amount,
            'paymentCurrency', 'USDT'
        )
    FROM (
        VALUES
            (597::BIGINT, 1376::BIGINT, 1323::BIGINT, first_tx_hash, 2928::NUMERIC),
            (600::BIGINT, 1377::BIGINT, 1324::BIGINT, second_tx_hash, 7941::NUMERIC)
    ) AS target(deal_id, watch_id, event_id, tx_hash, amount)
    WHERE NOT EXISTS (
        SELECT 1
        FROM deal_status_events existing
        WHERE existing.deal_id = target.deal_id
          AND existing.payload->>'repair' = repair_marker
    );

    RAISE NOTICE 'TAJO payment-watch shift repaired successfully';
END
$$;

COMMIT;

-- Verification output returned by psql after COMMIT.
SELECT
    q.id AS queue_id,
    q.deal_id,
    q.qty,
    q.status,
    q.payment_watch_id,
    q.payment_event_id,
    q.cancel_reason
FROM usdt_fulfillment_queue q
WHERE q.id IN (1, 4, 5)
ORDER BY q.id;

SELECT
    ds.id,
    ds.deal_id,
    ds.payment_event_id,
    ds.expected_qty,
    ds.actual_qty,
    ds.delta_qty,
    ds.review_status,
    ds.comment
FROM deal_settlements ds
WHERE ds.id IN (1, 2)
ORDER BY ds.id;
