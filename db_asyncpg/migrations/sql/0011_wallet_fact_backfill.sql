-- Preserve the last value from the legacy destructive table as an auditable
-- opening snapshot. Address is intentionally unknown for legacy observations.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM firm_wallet_facts
        WHERE UPPER(BTRIM(currency_code)) IN ('EUR', 'USDT', 'USD_BL', 'USD_WH')
          AND actual_qty < 0
    ) THEN
        RAISE EXCEPTION 'Cannot backfill negative legacy firm wallet fact';
    END IF;
END;
$$;

INSERT INTO firm_wallet_fact_snapshots(
    currency_code,
    actual_qty,
    observed_at,
    source,
    actor_user_id,
    comment,
    idempotency_key
)
SELECT
    UPPER(BTRIM(currency_code)),
    actual_qty,
    updated_at,
    'import',
    updated_by,
    CONCAT_WS(' | ', NULLIF(BTRIM(comment), ''), 'Legacy firm_wallet_facts opening'),
    'legacy-firm-wallet-fact:' || UPPER(BTRIM(currency_code))
FROM firm_wallet_facts
WHERE UPPER(BTRIM(currency_code)) IN ('EUR', 'USDT', 'USD_BL', 'USD_WH')
ON CONFLICT (idempotency_key) DO NOTHING;
