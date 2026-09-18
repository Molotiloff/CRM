DO $$
DECLARE
    correction RECORD;
    target_client_id BIGINT;
    target_account_id BIGINT;
    current_balance NUMERIC(38,8);
    corrected_balance NUMERIC(38,8);
    idempotency TEXT;
BEGIN
    FOR correction IN
        SELECT *
        FROM (
            VALUES
                (-5193430903::BIGINT, 'RUB'::TEXT, 370500.00::NUMERIC, FALSE, 'leviathan-rub'),
                (-5297126539::BIGINT, 'Б_КОСТЕТ'::TEXT, 1155107.00::NUMERIC, TRUE, 'b-kostet'),
                (-5297126539::BIGINT, 'Б_СОЧИ'::TEXT, 135400.00::NUMERIC, TRUE, 'b-sochi'),
                (-5068646239::BIGINT, 'ЗАЕМ'::TEXT, -251000.00::NUMERIC, TRUE, 'loan-monakh')
        ) AS cleanup(chat_id, currency_code, expected_balance, deactivate, source_key)
    LOOP
        idempotency := 'migration:0030:legacy-balance-cleanup:' || correction.source_key;
        target_client_id := NULL;
        target_account_id := NULL;
        current_balance := NULL;
        corrected_balance := NULL;

        SELECT client.id, account.id, account.balance
        INTO target_client_id, target_account_id, current_balance
        FROM clients AS client
        JOIN client_accounts AS account ON account.client_id = client.id
        WHERE client.chat_id = correction.chat_id
          AND account.currency_code = correction.currency_code
        FOR UPDATE OF account;

        IF target_account_id IS NULL THEN
            CONTINUE;
        END IF;

        IF EXISTS (
            SELECT 1
            FROM transactions
            WHERE client_id = target_client_id
              AND idempotency_key = idempotency
        ) THEN
            CONTINUE;
        END IF;

        corrected_balance := current_balance - correction.expected_balance;

        IF correction.deactivate AND corrected_balance <> 0 THEN
            RAISE EXCEPTION
                'Migration 0030 cannot deactivate % account for chat %: balance % minus historical correction % leaves %',
                correction.currency_code,
                correction.chat_id,
                current_balance,
                correction.expected_balance,
                corrected_balance;
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
            -correction.expected_balance,
            corrected_balance,
            CASE
                WHEN correction.deactivate
                    THEN 'Legacy duplicate reclassified to internal account by migration 0030'
                ELSE 'Manager-confirmed missing balance write-off by migration 0030'
            END,
            'migration',
            idempotency
        );

        UPDATE client_accounts
        SET balance = corrected_balance,
            is_active = CASE WHEN correction.deactivate THEN FALSE ELSE is_active END,
            deactivated_at = CASE
                WHEN correction.deactivate THEN NOW()
                ELSE deactivated_at
            END
        WHERE id = target_account_id;
    END LOOP;
END
$$;
