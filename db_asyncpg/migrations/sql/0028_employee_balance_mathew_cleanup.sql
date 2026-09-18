WITH employee_account AS (
    SELECT id, balance
    FROM internal_accounts
    WHERE currency_code = 'RUB'
      AND REGEXP_REPLACE(LOWER(BTRIM(name)), '[[:space:]]+', ' ', 'g') =
          'баланса мэтью'
)
INSERT INTO internal_account_moves(
    account_id,
    amount,
    balance_after,
    comment,
    idempotency_key
)
SELECT
    id,
    -balance,
    0,
    'Reclassified as client employee balance by migration 0028',
    'policy:0028:employee-client-reclass:' || id
FROM employee_account
WHERE balance <> 0
ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING;

UPDATE internal_accounts
SET balance = 0,
    is_active = FALSE
WHERE currency_code = 'RUB'
  AND REGEXP_REPLACE(LOWER(BTRIM(name)), '[[:space:]]+', ' ', 'g') =
      'баланса мэтью';
