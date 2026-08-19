ALTER TABLE firm_position_moves
    ADD COLUMN IF NOT EXISTS deal_leg_id BIGINT REFERENCES deal_legs(id),
    ADD COLUMN IF NOT EXISTS entry_rate NUMERIC(38,8),
    ADD COLUMN IF NOT EXISTS effective_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS created_by BIGINT REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS reversal_of_id BIGINT REFERENCES firm_position_moves(id),
    ADD COLUMN IF NOT EXISTS reason TEXT;

UPDATE firm_position_moves
SET effective_at = created_at
WHERE effective_at IS NULL;

UPDATE firm_position_moves
SET idempotency_key = 'legacy:firm-position:' || id::text
WHERE idempotency_key IS NULL;

ALTER TABLE firm_position_moves
    ALTER COLUMN effective_at SET NOT NULL,
    ALTER COLUMN idempotency_key SET NOT NULL;

ALTER TABLE firm_position_moves
    DROP CONSTRAINT IF EXISTS firm_position_moves_kind_check;

ALTER TABLE firm_position_moves
    ADD CONSTRAINT firm_position_moves_kind_check CHECK (
        kind IN (
            'opening', 'purchase', 'sale', 'adjust', 'reversal',
            'profit_capitalization'
        )
    ),
    ADD CONSTRAINT firm_position_moves_nonnegative_qty_check
        CHECK (qty_after >= 0) NOT VALID,
    ADD CONSTRAINT firm_position_moves_nonnegative_cost_check
        CHECK (rub_cost_after >= 0) NOT VALID,
    ADD CONSTRAINT firm_position_moves_adjust_reason_check
        CHECK (kind <> 'adjust' OR NULLIF(BTRIM(reason), '') IS NOT NULL) NOT VALID,
    ADD CONSTRAINT firm_position_moves_reversal_source_check
        CHECK (
            kind <> 'reversal'
            OR (reversal_of_id IS NOT NULL AND NULLIF(BTRIM(reason), '') IS NOT NULL)
        ) NOT VALID;

CREATE UNIQUE INDEX IF NOT EXISTS uq_firm_position_moves_idempotency
    ON firm_position_moves(idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_firm_position_moves_reversal
    ON firm_position_moves(reversal_of_id)
    WHERE reversal_of_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_firm_position_moves_deal_leg
    ON firm_position_moves(deal_leg_id)
    WHERE deal_leg_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_firm_position_moves_cur_desc
    ON firm_position_moves(currency_code, id DESC);
CREATE INDEX IF NOT EXISTS idx_firm_position_moves_deal
    ON firm_position_moves(deal_id)
    WHERE deal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_firm_position_moves_effective_at
    ON firm_position_moves(effective_at);

CREATE OR REPLACE FUNCTION prevent_firm_position_move_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'firm_position_moves is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_firm_position_moves_append_only ON firm_position_moves;
CREATE TRIGGER trg_firm_position_moves_append_only
BEFORE UPDATE OR DELETE ON firm_position_moves
FOR EACH ROW
EXECUTE FUNCTION prevent_firm_position_move_mutation();
