ALTER TABLE deals DROP CONSTRAINT IF EXISTS deals_deal_type_check;
ALTER TABLE deals ADD CONSTRAINT deals_deal_type_check CHECK (deal_type IN (
    'sale', 'purchase', 'deposit', 'withdrawal', 'delivery', 'transfer_city',
    'conversion', 'yuan', 'invoice', 'profit', 'best_change'
));

CREATE TABLE IF NOT EXISTS best_change_account_moves (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    account_code TEXT NOT NULL CHECK (account_code IN (
        'purchase_tym', 'sale_tym', 'purchase_chlb', 'sale_chlb', 'platform_fee'
    )),
    currency_code TEXT NOT NULL CHECK (currency_code IN ('RUB', 'USDT')),
    amount NUMERIC(38,8) NOT NULL CHECK (amount <> 0),
    balance_after NUMERIC(38,8) NOT NULL,
    deal_id BIGINT REFERENCES deals(id),
    actor_user_id BIGINT REFERENCES users(id),
    comment TEXT,
    idempotency_key TEXT NOT NULL,
    reversal_of_id BIGINT REFERENCES best_change_account_moves(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (account_code = 'platform_fee' AND currency_code = 'USDT')
        OR (account_code <> 'platform_fee' AND currency_code = 'RUB')
    ),
    CHECK (reversal_of_id IS NULL OR NULLIF(BTRIM(comment), '') IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_best_change_moves_account
    ON best_change_account_moves(chat_id, account_code, id);
CREATE INDEX IF NOT EXISTS idx_best_change_moves_deal
    ON best_change_account_moves(deal_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_best_change_moves_idempotency
    ON best_change_account_moves(idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_best_change_moves_reversal
    ON best_change_account_moves(reversal_of_id) WHERE reversal_of_id IS NOT NULL;

CREATE OR REPLACE FUNCTION prevent_best_change_move_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'best_change_account_moves is append-only';
END;
$$;
DROP TRIGGER IF EXISTS trg_best_change_moves_append_only ON best_change_account_moves;
CREATE TRIGGER trg_best_change_moves_append_only
BEFORE UPDATE OR DELETE ON best_change_account_moves
FOR EACH ROW
EXECUTE FUNCTION prevent_best_change_move_mutation();
