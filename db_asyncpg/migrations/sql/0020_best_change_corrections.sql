CREATE TABLE IF NOT EXISTS best_change_deal_corrections (
    id BIGSERIAL PRIMARY KEY,
    original_deal_id BIGINT NOT NULL REFERENCES deals(id),
    replacement_deal_id BIGINT NOT NULL UNIQUE REFERENCES deals(id),
    actor_user_id BIGINT NOT NULL REFERENCES users(id),
    source_ref TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL CHECK (NULLIF(BTRIM(reason), '') IS NOT NULL),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (original_deal_id)
);
CREATE INDEX IF NOT EXISTS idx_best_change_corrections_original
    ON best_change_deal_corrections(original_deal_id);

CREATE OR REPLACE FUNCTION prevent_best_change_correction_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'best_change_deal_corrections is append-only';
END;
$$;
DROP TRIGGER IF EXISTS trg_best_change_corrections_append_only
    ON best_change_deal_corrections;
CREATE TRIGGER trg_best_change_corrections_append_only
BEFORE UPDATE OR DELETE ON best_change_deal_corrections
FOR EACH ROW
EXECUTE FUNCTION prevent_best_change_correction_mutation();
