CREATE OR REPLACE VIEW crm_sales AS
SELECT d.id,
       d.deal_no,
       d.deal_at,
       btrim(d.city) AS city,
       d.client_id,
       d.counterparty_id,
       d.status,
       upper(btrim(d.body->>'currency'))  AS currency_code,
       (d.body->>'qty')::numeric          AS qty,
       (d.body->>'entry_rate')::numeric   AS entry_rate,
       (d.body->>'exit_rate')::numeric    AS exit_rate,
       (d.body->>'buy_amount')::numeric   AS buy_amount,
       (d.body->>'sale_amount')::numeric  AS sale_amount,
       (d.body->>'kt_spread')::numeric    AS kt_spread,
       (d.body->>'kt_amount')::numeric    AS kt_amount,
       d.profit,
       (d.body->>'our_profit')::numeric   AS our_profit
FROM deals d
WHERE d.deal_type = 'sale' AND d.status = 'done';

CREATE OR REPLACE VIEW crm_purchases AS
SELECT d.id,
       d.deal_no,
       d.deal_at,
       btrim(d.city) AS city,
       d.client_id,
       d.counterparty_id,
       d.status,
       upper(btrim(d.body->>'currency')) AS currency_code,
       (d.body->>'qty')::numeric         AS qty,
       (d.body->>'rate')::numeric        AS rate,
       (d.body->>'rub_amount')::numeric  AS rub_amount,
       d.body->>'seller'                 AS seller
FROM deals d
WHERE d.deal_type = 'purchase' AND d.status = 'done';
