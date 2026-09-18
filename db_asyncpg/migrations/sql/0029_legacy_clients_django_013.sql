ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ;

INSERT INTO clients(chat_id, name, client_group, is_active, deactivated_at)
VALUES
    (-5013189281, 'SkyEx | Джанго', NULL, TRUE, NULL),
    (-5004103796, '013 | SkyEx', NULL, TRUE, NULL)
ON CONFLICT (chat_id) DO UPDATE
SET name = EXCLUDED.name,
    is_active = TRUE,
    deactivated_at = NULL;

INSERT INTO client_accounts(client_id, currency_code, precision)
SELECT client.id, currency.currency_code, currency.precision
FROM clients AS client
CROSS JOIN (
    VALUES
        ('USD', 2),
        ('USDW', 2),
        ('USDT', 2),
        ('RUB', 2),
        ('EUR', 2),
        ('EUR500', 2),
        ('РУБПЕР', 2)
) AS currency(currency_code, precision)
WHERE client.chat_id IN (-5013189281, -5004103796)
ON CONFLICT (client_id, currency_code) DO UPDATE
SET is_active = TRUE,
    precision = EXCLUDED.precision,
    deactivated_at = NULL;

DO $$
DECLARE
    opening RECORD;
    target_client_id BIGINT;
    target_account_id BIGINT;
    current_balance NUMERIC(38,8);
BEGIN
    FOR opening IN
        SELECT *
        FROM (
            VALUES
                (-5013189281::BIGINT, 'RUB'::TEXT, -275822.20::NUMERIC, 'django-rub'::TEXT),
                (-5004103796::BIGINT, 'RUB'::TEXT, -150000.00::NUMERIC, '013-rub'::TEXT),
                (-5004103796::BIGINT, 'USDT'::TEXT, 7.00::NUMERIC, '013-usdt'::TEXT)
        ) AS values_to_import(chat_id, currency_code, amount, source_key)
    LOOP
        SELECT client.id, account.id, account.balance
        INTO target_client_id, target_account_id, current_balance
        FROM clients AS client
        JOIN client_accounts AS account ON account.client_id = client.id
        WHERE client.chat_id = opening.chat_id
          AND account.currency_code = opening.currency_code
        FOR UPDATE OF account;

        IF EXISTS (
            SELECT 1
            FROM transactions
            WHERE client_id = target_client_id
              AND idempotency_key = 'migration:0029:legacy-client-opening:' || opening.source_key
        ) THEN
            CONTINUE;
        END IF;

        IF current_balance <> 0 THEN
            RAISE EXCEPTION
                'Migration 0029 expected zero % balance for chat %, got %',
                opening.currency_code,
                opening.chat_id,
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
            TIMESTAMPTZ '2026-09-07 20:40:10+05',
            opening.amount,
            opening.amount,
            'Opening balance imported from SkyEx September 2026 workbook',
            'import',
            'migration:0029:legacy-client-opening:' || opening.source_key
        );

        UPDATE client_accounts
        SET balance = opening.amount
        WHERE id = target_account_id;
    END LOOP;
END
$$;
