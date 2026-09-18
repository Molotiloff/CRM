ALTER TABLE cash_chat_registry
    ADD COLUMN IF NOT EXISTS cash_currency_codes TEXT[];

DROP INDEX IF EXISTS uq_cash_chat_registry_active_city;

CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_chat_registry_active_full_city
    ON cash_chat_registry(LOWER(BTRIM(city)))
    WHERE is_active AND cash_currency_codes IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_chat_registry_active_location
    ON cash_chat_registry(LOWER(BTRIM(city)), LOWER(BTRIM(location_name)))
    WHERE is_active;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_cash_chat_registry_currency_scope'
          AND conrelid = 'cash_chat_registry'::regclass
    ) THEN
        ALTER TABLE cash_chat_registry
            ADD CONSTRAINT ck_cash_chat_registry_currency_scope
            CHECK (
                cash_currency_codes IS NULL
                OR (
                    CARDINALITY(cash_currency_codes) > 0
                    AND ARRAY_POSITION(cash_currency_codes, NULL) IS NULL
                    AND cash_currency_codes::text = UPPER(cash_currency_codes::text)
                )
            );
    END IF;
END $$;
