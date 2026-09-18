ALTER TABLE cash_desk_moves
    ADD COLUMN IF NOT EXISTS operation_kind TEXT NOT NULL DEFAULT 'transfer',
    ADD COLUMN IF NOT EXISTS effective_at DATE,
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS reversal_of_id BIGINT REFERENCES cash_desk_moves(id);

UPDATE cash_desk_moves SET effective_at = created_at::date WHERE effective_at IS NULL;
ALTER TABLE cash_desk_moves ALTER COLUMN effective_at SET DEFAULT CURRENT_DATE;
ALTER TABLE cash_desk_moves ALTER COLUMN effective_at SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_desk_moves_idempotency
    ON cash_desk_moves(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_desk_moves_reversal
    ON cash_desk_moves(reversal_of_id) WHERE reversal_of_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_cash_desk_moves_operation_kind'
          AND conrelid = 'cash_desk_moves'::regclass
    ) THEN
        ALTER TABLE cash_desk_moves
            ADD CONSTRAINT ck_cash_desk_moves_operation_kind
            CHECK (operation_kind IN (
                'transfer', 'opening', 'inflow', 'outflow', 'reversal'
            ));
    END IF;
END $$;

DO $$
DECLARE
    item RECORD;
    account_id BIGINT;
    desk_id BIGINT;
BEGIN
    FOR item IN
        SELECT * FROM (VALUES
            ('RUB Мск Поэты', 'Поэты', -57565.00::numeric, 'internal-moscow-poets'),
            ('RUB Мск BS', 'BS', 82408.00::numeric, 'internal-moscow-bs')
        ) AS source(account_name, desk_name, opening_amount, source_key)
    LOOP
        SELECT id INTO account_id FROM internal_accounts WHERE name = item.account_name;
        IF account_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM internal_account_moves
            WHERE idempotency_key = 'correction:0021:' || item.source_key
        ) THEN
            IF (SELECT balance FROM internal_accounts WHERE id = account_id) <> item.opening_amount THEN
                RAISE EXCEPTION 'Unexpected internal balance for %', item.account_name;
            END IF;
            INSERT INTO internal_account_moves(
                account_id, amount, balance_after, comment, idempotency_key
            ) VALUES (
                account_id, -item.opening_amount, 0,
                '0021: перенесено из internal в московскую RUB-кассу',
                'correction:0021:' || item.source_key
            );
            UPDATE internal_accounts SET balance = 0, is_active = FALSE
            WHERE id = account_id;
        END IF;

        INSERT INTO cash_desks(city, name, currency_code)
        VALUES ('мск', item.desk_name, 'RUB')
        ON CONFLICT (city, name, currency_code) DO UPDATE SET is_active = TRUE
        RETURNING id INTO desk_id;

        -- Real opening balances are migrated only when the matching imported
        -- internal account exists. A clean installation keeps both desks at zero.
        IF account_id IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT 1 FROM cash_desk_moves
                WHERE idempotency_key = 'opening:0021:' || item.source_key
            ) THEN
                IF (SELECT balance FROM cash_desks WHERE id = desk_id) <> 0 THEN
                    RAISE EXCEPTION 'Cash desk % already has a nonzero balance', item.desk_name;
                END IF;
                INSERT INTO cash_desk_moves(
                    from_desk_id, to_desk_id, amount,
                    balance_after_from, balance_after_to,
                    comment, operation_kind, effective_at, idempotency_key
                ) VALUES (
                    CASE WHEN item.opening_amount < 0 THEN desk_id END,
                    CASE WHEN item.opening_amount > 0 THEN desk_id END,
                    ABS(item.opening_amount),
                    CASE WHEN item.opening_amount < 0 THEN item.opening_amount END,
                    CASE WHEN item.opening_amount > 0 THEN item.opening_amount END,
                    'Opening из Google Sheets на 26.08.2026',
                    'opening', DATE '2026-08-26', 'opening:0021:' || item.source_key
                );
                UPDATE cash_desks SET balance = item.opening_amount WHERE id = desk_id;
            END IF;
        END IF;
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION prevent_cash_desk_move_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'cash_desk_moves is append-only';
END;
$$;
DROP TRIGGER IF EXISTS trg_cash_desk_moves_append_only ON cash_desk_moves;
CREATE TRIGGER trg_cash_desk_moves_append_only
BEFORE UPDATE OR DELETE ON cash_desk_moves
FOR EACH ROW EXECUTE FUNCTION prevent_cash_desk_move_mutation();
