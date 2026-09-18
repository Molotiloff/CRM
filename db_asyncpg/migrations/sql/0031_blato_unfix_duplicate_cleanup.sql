DO $$
DECLARE
    target_account_id BIGINT;
    current_balance NUMERIC(38,8);
    target_is_active BOOLEAN;
    idempotency CONSTANT TEXT := 'migration:0031:blato-unfix-duplicate-cleanup';
BEGIN
    SELECT id, balance, is_active
    INTO target_account_id, current_balance, target_is_active
    FROM internal_accounts
    WHERE name = 'USDT: Blato расфикс'
      AND currency_code = 'USDT'
    FOR UPDATE;

    IF target_account_id IS NULL THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM internal_account_moves
        WHERE idempotency_key = idempotency
    ) THEN
        RETURN;
    END IF;

    IF current_balance = 0 THEN
        IF target_is_active THEN
            UPDATE internal_accounts
            SET is_active = FALSE
            WHERE id = target_account_id;
        END IF;
        RETURN;
    END IF;

    IF current_balance <> -250.00000000 THEN
        RAISE EXCEPTION
            'Migration 0031 expected USDT: Blato расфикс balance -250, got %',
            current_balance;
    END IF;

    INSERT INTO internal_account_moves(
        account_id,
        amount,
        balance_after,
        comment,
        idempotency_key
    )
    VALUES (
        target_account_id,
        -current_balance,
        0,
        'Duplicate of client ledger command -250 | расфикс; confirmed by manager',
        idempotency
    );

    UPDATE internal_accounts
    SET balance = 0,
        is_active = FALSE
    WHERE id = target_account_id;
END
$$;
