ALTER TABLE deals DROP CONSTRAINT IF EXISTS deals_status_check;
ALTER TABLE deals
    ADD CONSTRAINT deals_status_check CHECK (
        status IN (
            'new', 'fixed', 'awaiting_payment', 'balance_check',
            'in_delivery', 'ready_for_cash_settlement', 'done', 'canceled'
        )
    );

CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_deals_request_id
    ON deals ((body->>'req_id'))
    WHERE source_kind = 'cash' AND NULLIF(BTRIM(body->>'req_id'), '') IS NOT NULL;

CREATE TABLE IF NOT EXISTS cash_settlements (
    id BIGSERIAL PRIMARY KEY,
    deal_id BIGINT NOT NULL UNIQUE REFERENCES deals(id),
    request_id TEXT NOT NULL UNIQUE,
    request_kind TEXT NOT NULL CHECK (request_kind IN ('dep', 'wd')),
    city TEXT NOT NULL CHECK (NULLIF(BTRIM(city), '') IS NOT NULL),
    currency_code TEXT NOT NULL CHECK (currency_code = UPPER(BTRIM(currency_code))),
    expected_qty NUMERIC(38,8) NOT NULL CHECK (expected_qty > 0),
    actual_qty NUMERIC(38,8) NOT NULL CHECK (actual_qty > 0),
    command_chat_id BIGINT NOT NULL,
    command_message_id BIGINT NOT NULL,
    cash_transaction_id BIGINT NOT NULL UNIQUE REFERENCES transactions(id),
    client_transaction_id BIGINT NOT NULL UNIQUE REFERENCES transactions(id),
    position_move_id BIGINT UNIQUE REFERENCES firm_position_moves(id),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    settled_by_tg_user_id BIGINT,
    settled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (command_chat_id, command_message_id)
);
