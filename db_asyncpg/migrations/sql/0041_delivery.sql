CREATE TABLE delivery_courier_wallets (
    tg_user_id BIGINT PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES clients(id),
    updated_by BIGINT NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE delivery_postings (
    deal_id BIGINT PRIMARY KEY REFERENCES deals(id),
    courier_client_id BIGINT NOT NULL REFERENCES clients(id),
    amount_rub NUMERIC(38,8) NOT NULL CHECK(amount_rub > 0),
    reward_rub NUMERIC(38,8) NOT NULL CHECK(reward_rub >= 0),
    cash_transaction_id BIGINT NOT NULL UNIQUE REFERENCES transactions(id),
    client_transaction_id BIGINT NOT NULL UNIQUE REFERENCES transactions(id),
    reward_transaction_id BIGINT UNIQUE REFERENCES transactions(id),
    expense_id BIGINT UNIQUE REFERENCES expenses(id),
    posted_by BIGINT NOT NULL REFERENCES users(id),
    posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((reward_rub=0 AND reward_transaction_id IS NULL AND expense_id IS NULL)
        OR (reward_rub>0 AND reward_transaction_id IS NOT NULL AND expense_id IS NOT NULL))
);
