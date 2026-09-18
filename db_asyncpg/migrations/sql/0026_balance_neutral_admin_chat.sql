ALTER TABLE cash_settlements
    ALTER COLUMN client_transaction_id DROP NOT NULL;

UPDATE clients
SET client_group = 'internal_wallet'
WHERE chat_id = -1003554176285
  AND name = 'Админка SkyEx';
