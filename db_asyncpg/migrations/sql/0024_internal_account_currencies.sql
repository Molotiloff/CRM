ALTER TABLE internal_accounts
    ADD COLUMN IF NOT EXISTS currency_code TEXT NOT NULL DEFAULT 'RUB';

ALTER TABLE internal_accounts
    DROP CONSTRAINT IF EXISTS ck_internal_accounts_currency;

ALTER TABLE internal_accounts
    ADD CONSTRAINT ck_internal_accounts_currency CHECK (
        currency_code IN ('RUB', 'EUR', 'USDT', 'USD_BL', 'USD_WH')
    );

CREATE INDEX IF NOT EXISTS idx_internal_accounts_active_currency
    ON internal_accounts(currency_code)
    WHERE is_active;

