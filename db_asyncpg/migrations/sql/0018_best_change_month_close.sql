CREATE TABLE IF NOT EXISTS best_change_month_closures (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    period_month DATE NOT NULL CHECK (period_month = date_trunc('month', period_month)::date),
    purchase_tym_rub NUMERIC(38,8) NOT NULL,
    sale_tym_rub NUMERIC(38,8) NOT NULL,
    purchase_chlb_rub NUMERIC(38,8) NOT NULL,
    sale_chlb_rub NUMERIC(38,8) NOT NULL,
    profit_pool_rub NUMERIC(38,8) NOT NULL,
    partner_share_rub NUMERIC(38,8) NOT NULL,
    skyex_profit_rub NUMERIC(38,8) NOT NULL,
    platform_fee_accrued_usdt NUMERIC(38,8) NOT NULL,
    platform_fee_paid_usdt NUMERIC(38,8) NOT NULL DEFAULT 0,
    platform_fee_delta_usdt NUMERIC(38,8) NOT NULL,
    payment_reference TEXT,
    deal_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(deal_ids) = 'array'),
    status TEXT NOT NULL DEFAULT 'closed' CHECK (status IN ('closed', 'reversed')),
    closed_by BIGINT NOT NULL REFERENCES users(id),
    closed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    idempotency_key TEXT NOT NULL UNIQUE,
    reversal_of_id BIGINT REFERENCES best_change_month_closures(id),
    comment TEXT,
    CHECK (profit_pool_rub = purchase_tym_rub + sale_tym_rub
                            + purchase_chlb_rub + sale_chlb_rub),
    CHECK (profit_pool_rub = partner_share_rub + skyex_profit_rub),
    CHECK (platform_fee_paid_usdt >= 0),
    CHECK (platform_fee_delta_usdt = platform_fee_accrued_usdt - platform_fee_paid_usdt),
    CHECK (reversal_of_id IS NULL OR NULLIF(BTRIM(comment), '') IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_best_change_active_month_close
    ON best_change_month_closures(chat_id, period_month)
    WHERE status = 'closed' AND reversal_of_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_best_change_month_close_reversal
    ON best_change_month_closures(reversal_of_id)
    WHERE reversal_of_id IS NOT NULL;

ALTER TABLE best_change_account_moves
    ADD COLUMN IF NOT EXISTS closure_id BIGINT REFERENCES best_change_month_closures(id);
CREATE INDEX IF NOT EXISTS idx_best_change_moves_closure
    ON best_change_account_moves(closure_id);
