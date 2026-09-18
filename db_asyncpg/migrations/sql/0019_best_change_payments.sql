CREATE TABLE IF NOT EXISTS best_change_payments (
    id BIGSERIAL PRIMARY KEY,
    closure_id BIGINT NOT NULL REFERENCES best_change_month_closures(id),
    payment_kind TEXT NOT NULL CHECK (payment_kind IN ('partner', 'coindrop')),
    currency_code TEXT NOT NULL CHECK (currency_code IN ('RUB', 'USDT')),
    amount NUMERIC(38,8) NOT NULL CHECK (amount <> 0),
    payment_reference TEXT NOT NULL CHECK (NULLIF(BTRIM(payment_reference), '') IS NOT NULL),
    actor_user_id BIGINT NOT NULL REFERENCES users(id),
    source_ref TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    reversal_of_id BIGINT REFERENCES best_change_payments(id),
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (payment_kind = 'partner' AND currency_code = 'RUB')
        OR (payment_kind = 'coindrop' AND currency_code = 'USDT')
    ),
    CHECK (
        (reversal_of_id IS NULL AND amount > 0)
        OR (
            reversal_of_id IS NOT NULL
            AND amount < 0
            AND NULLIF(BTRIM(comment), '') IS NOT NULL
        )
    )
);
CREATE INDEX IF NOT EXISTS idx_best_change_payments_closure
    ON best_change_payments(closure_id, payment_kind, id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_best_change_payment_source
    ON best_change_payments(source_ref);
CREATE UNIQUE INDEX IF NOT EXISTS uq_best_change_payment_reversal
    ON best_change_payments(reversal_of_id)
    WHERE reversal_of_id IS NOT NULL;

CREATE OR REPLACE FUNCTION prevent_best_change_payment_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'best_change_payments is append-only';
END;
$$;
DROP TRIGGER IF EXISTS trg_best_change_payments_append_only ON best_change_payments;
CREATE TRIGGER trg_best_change_payments_append_only
BEFORE UPDATE OR DELETE ON best_change_payments
FOR EACH ROW
EXECUTE FUNCTION prevent_best_change_payment_mutation();
