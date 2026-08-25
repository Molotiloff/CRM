-- A confirmed blockchain transfer is immutable evidence and may settle only one watch.
CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_watch_events_tx_hash
    ON payment_watch_events(tx_hash);
