ALTER TABLE profit_usdt_accruals DROP CONSTRAINT IF EXISTS profit_usdt_accruals_qty_check;
ALTER TABLE profit_usdt_accruals DROP CONSTRAINT IF EXISTS profit_usdt_accruals_capitalization_status_check;
DO $$
DECLARE item record;
BEGIN
    FOR item IN SELECT conname FROM pg_constraint
        WHERE conrelid='profit_usdt_accruals'::regclass AND contype='c'
          AND pg_get_constraintdef(oid) LIKE '%capitalized_at%'
    LOOP
        EXECUTE format('ALTER TABLE profit_usdt_accruals DROP CONSTRAINT %I', item.conname);
    END LOOP;
END $$;
ALTER TABLE profit_usdt_accruals
    ADD CONSTRAINT profit_usdt_accruals_capitalization_status_check CHECK (capitalization_status IN ('pending','capitalized','cash_only')),
    ADD CONSTRAINT profit_usdt_accruals_qty_check CHECK (qty > 0 OR capitalization_status='cash_only'),
    ADD CONSTRAINT profit_usdt_accruals_capitalization_audit_check CHECK (
        (capitalization_status IN ('pending','cash_only') AND capitalized_at IS NULL AND capitalization_move_id IS NULL)
        OR (capitalization_status='capitalized' AND valuation_rate IS NOT NULL AND capitalized_at IS NOT NULL AND capitalization_move_id IS NOT NULL)
    ),
    ADD CONSTRAINT profit_usdt_accruals_cash_received_check CHECK (capitalization_status <> 'cash_only' OR settlement_status='received');
