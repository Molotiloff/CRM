CREATE TABLE IF NOT EXISTS client_transfer_adjustments (
    id BIGSERIAL PRIMARY KEY,
    deal_id BIGINT NOT NULL REFERENCES deals(id),
    source_ref TEXT NOT NULL UNIQUE,
    old_amount NUMERIC(38,8) NOT NULL,
    new_amount NUMERIC(38,8) NOT NULL,
    actor_tg_user_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_client_transfer_adjustments_deal
    ON client_transfer_adjustments(deal_id, id);
