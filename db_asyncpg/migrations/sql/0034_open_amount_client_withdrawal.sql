-- /отпр without an amount watches for any confirmed transfer; zero means unknown
-- expected quantity, not a zero-value accounting transaction.
ALTER TABLE usdt_fulfillment_queue
    DROP CONSTRAINT IF EXISTS usdt_fulfillment_queue_qty_check;

ALTER TABLE usdt_fulfillment_queue
    ADD CONSTRAINT usdt_fulfillment_queue_qty_check
    CHECK (
        (request_kind = 'sale' AND qty > 0)
        OR (request_kind = 'client_withdrawal' AND qty >= 0)
    );
