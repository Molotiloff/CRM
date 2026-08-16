BEGIN;

ALTER TABLE payment_watches
    ADD COLUMN IF NOT EXISTS deal_id BIGINT REFERENCES deals(id);

CREATE INDEX IF NOT EXISTS idx_payment_watches_deal
    ON payment_watches(deal_id, created_at DESC)
    WHERE deal_id IS NOT NULL;

COMMIT;
