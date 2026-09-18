WITH employee_accounts AS (
    SELECT id, balance
    FROM internal_accounts
    WHERE currency_code = 'RUB'
      AND REGEXP_REPLACE(LOWER(BTRIM(name)), '[[:space:]]+', ' ', 'g') IN (
          'баланс вв',
          'баланс никита',
          'баланс влад',
          'баланс лев',
          'баланс ваня support',
          'баланс монах',
          'баланс миша',
          'баланс саша члб',
          'баланса мэттью',
          'баланс тенаклиус'
      )
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
    'Reclassified as client employee balance by migration 0027',
    'policy:0027:employee-client-reclass:' || id
FROM employee_accounts
WHERE balance <> 0
ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING;

UPDATE internal_accounts
SET balance = 0,
    is_active = FALSE
WHERE currency_code = 'RUB'
  AND REGEXP_REPLACE(LOWER(BTRIM(name)), '[[:space:]]+', ' ', 'g') IN (
      'баланс вв',
      'баланс никита',
      'баланс влад',
      'баланс лев',
      'баланс ваня support',
      'баланс монах',
      'баланс миша',
      'баланс саша члб',
      'баланса мэттью',
      'баланс тенаклиус'
  );
