DO $$
DECLARE
    target_client_id BIGINT;
    target_account_id BIGINT;
    current_balance NUMERIC(38,8);
    correction_amount CONSTANT NUMERIC(38,8) := 370500.00000000;
    original_idempotency CONSTANT TEXT := 'migration:0030:legacy-balance-cleanup:leviathan-rub';
    reversal_idempotency CONSTANT TEXT := 'migration:0033:leviathan-duplicate-writeoff-reversal';
BEGIN
    SELECT client.id, account.id, account.balance
    INTO target_client_id, target_account_id, current_balance
    FROM clients AS client
    JOIN client_accounts AS account ON account.client_id = client.id
    WHERE client.chat_id = -5193430903
      AND account.currency_code = 'RUB'
    FOR UPDATE OF account;

    IF target_account_id IS NULL THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM transactions
        WHERE client_id = target_client_id
          AND idempotency_key = reversal_idempotency
    ) THEN
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM transactions
        WHERE client_id = target_client_id
          AND account_id = target_account_id
          AND idempotency_key = original_idempotency
          AND amount = -correction_amount
    ) THEN
        RETURN;
    END IF;

    -- The manager had already written the balance off before migration 0030.
    -- Reverse 0030 only when that earlier command demonstrably left the account at zero.
    IF NOT EXISTS (
        SELECT 1
        FROM transactions
        WHERE client_id = target_client_id
          AND account_id = target_account_id
          AND source = 'command'
          AND amount = -correction_amount
          AND balance_after = 0
    ) THEN
        RAISE EXCEPTION
            'Migration 0033 found the 0030 Leviathan correction without the manager write-off';
    END IF;

    IF current_balance <> -correction_amount THEN
        RAISE EXCEPTION
            'Migration 0033 expected Leviathan RUB balance %, got %',
            -correction_amount,
            current_balance;
    END IF;

    INSERT INTO transactions(
        client_id,
        account_id,
        txn_at,
        amount,
        balance_after,
        comment,
        source,
        idempotency_key
    )
    VALUES (
        target_client_id,
        target_account_id,
        NOW(),
        correction_amount,
        0,
        'Reversal of duplicate Leviathan write-off from migration 0030',
        'migration',
        reversal_idempotency
    );

    UPDATE client_accounts
    SET balance = 0
    WHERE id = target_account_id;
END
$$;
