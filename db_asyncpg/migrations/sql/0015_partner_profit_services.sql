-- Idempotency for the partner RUB leg created together with a purchase request.
ALTER TABLE internal_account_moves
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_internal_account_moves_idempotency
    ON internal_account_moves(idempotency_key)
    WHERE idempotency_key IS NOT NULL;
