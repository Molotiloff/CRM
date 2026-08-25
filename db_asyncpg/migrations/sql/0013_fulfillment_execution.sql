ALTER TABLE usdt_fulfillment_queue
    ADD COLUMN IF NOT EXISTS payment_watch_id BIGINT UNIQUE REFERENCES payment_watches(id),
    ADD COLUMN IF NOT EXISTS payment_event_id BIGINT UNIQUE REFERENCES payment_watch_events(id),
    ADD COLUMN IF NOT EXISTS position_move_id BIGINT UNIQUE REFERENCES firm_position_moves(id),
    ADD COLUMN IF NOT EXISTS completed_by BIGINT REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS wallet_source TEXT
        CHECK (wallet_source IN ('firm_wallet', 'external'));

ALTER TABLE usdt_fulfillment_queue
    DROP CONSTRAINT IF EXISTS usdt_fulfillment_queue_completion_event_check;
ALTER TABLE usdt_fulfillment_queue
    ADD CONSTRAINT usdt_fulfillment_queue_completion_event_check
    CHECK (status <> 'completed' OR payment_event_id IS NOT NULL);
