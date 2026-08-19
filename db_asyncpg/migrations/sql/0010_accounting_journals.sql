-- Physical cash locations backed by existing client ledger accounts.
CREATE TABLE IF NOT EXISTS cash_chat_registry (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL UNIQUE,
    client_id BIGINT NOT NULL UNIQUE REFERENCES clients(id),
    city TEXT NOT NULL CHECK (NULLIF(BTRIM(city), '') IS NOT NULL),
    location_name TEXT NOT NULL CHECK (NULLIF(BTRIM(location_name), '') IS NOT NULL),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deactivated_at TIMESTAMPTZ,
    CHECK ((is_active AND deactivated_at IS NULL) OR (NOT is_active AND deactivated_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_chat_registry_active_city
    ON cash_chat_registry(LOWER(BTRIM(city)))
    WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_cash_chat_registry_active_client
    ON cash_chat_registry(client_id)
    WHERE is_active;

-- Active and historical addresses controlled by the firm.
CREATE TABLE IF NOT EXISTS firm_wallet_addresses (
    id BIGSERIAL PRIMARY KEY,
    network TEXT NOT NULL CHECK (NULLIF(BTRIM(network), '') IS NOT NULL),
    address TEXT NOT NULL CHECK (NULLIF(BTRIM(address), '') IS NOT NULL),
    active_from TIMESTAMPTZ NOT NULL,
    active_to TIMESTAMPTZ,
    reason TEXT NOT NULL CHECK (NULLIF(BTRIM(reason), '') IS NOT NULL),
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (network, address),
    CHECK (active_to IS NULL OR active_to > active_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_firm_wallet_addresses_active_network
    ON firm_wallet_addresses(UPPER(BTRIM(network)))
    WHERE active_to IS NULL;

CREATE TABLE IF NOT EXISTS firm_wallet_fact_snapshots (
    id BIGSERIAL PRIMARY KEY,
    currency_code TEXT NOT NULL CHECK (
        currency_code = UPPER(BTRIM(currency_code))
        AND currency_code IN ('EUR', 'USDT', 'USD_BL', 'USD_WH')
    ),
    address_id BIGINT REFERENCES firm_wallet_addresses(id),
    actual_qty NUMERIC(38,8) NOT NULL CHECK (actual_qty >= 0),
    observed_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL CHECK (
        source IN ('manual', 'payment_watch', 'tronscan', 'cash_chat_ledger', 'import')
    ),
    actor_user_id BIGINT REFERENCES users(id),
    comment TEXT,
    idempotency_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_firm_wallet_fact_snapshots_latest
    ON firm_wallet_fact_snapshots(currency_code, observed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_firm_wallet_fact_snapshots_address
    ON firm_wallet_fact_snapshots(address_id, observed_at DESC)
    WHERE address_id IS NOT NULL;

-- Expected versus observed settlement, including review evidence and resolution.
CREATE TABLE IF NOT EXISTS deal_settlements (
    id BIGSERIAL PRIMARY KEY,
    deal_id BIGINT NOT NULL REFERENCES deals(id),
    payment_event_id BIGINT REFERENCES payment_watch_events(id),
    direction TEXT NOT NULL CHECK (direction IN ('incoming', 'outgoing')),
    currency_code TEXT NOT NULL CHECK (NULLIF(BTRIM(currency_code), '') IS NOT NULL),
    expected_qty NUMERIC(38,8) NOT NULL CHECK (expected_qty > 0),
    actual_qty NUMERIC(38,8) NOT NULL CHECK (actual_qty > 0),
    delta_qty NUMERIC(38,8) GENERATED ALWAYS AS (actual_qty - expected_qty) STORED,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_status TEXT NOT NULL CHECK (
        review_status IN ('matched', 'needs_review', 'resolved')
    ),
    resolution TEXT CHECK (
        resolution IN ('accept_actual', 'amend_unposted', 'cancel_and_recreate')
    ),
    resolved_by BIGINT REFERENCES users(id),
    resolved_at TIMESTAMPTZ,
    comment TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (review_status = 'matched' AND delta_qty = 0 AND resolution IS NULL
            AND resolved_by IS NULL AND resolved_at IS NULL)
        OR (review_status = 'needs_review' AND delta_qty <> 0 AND resolution IS NULL
            AND resolved_by IS NULL AND resolved_at IS NULL)
        OR (review_status = 'resolved' AND resolution IS NOT NULL
            AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_deal_settlements_payment_event
    ON deal_settlements(payment_event_id)
    WHERE payment_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_deal_settlements_deal
    ON deal_settlements(deal_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deal_settlements_review
    ON deal_settlements(created_at)
    WHERE review_status = 'needs_review';

-- One manually reorderable queue without priority classes.
CREATE TABLE IF NOT EXISTS usdt_fulfillment_queue (
    id BIGSERIAL PRIMARY KEY,
    deal_id BIGINT NOT NULL REFERENCES deals(id),
    request_kind TEXT NOT NULL CHECK (request_kind IN ('sale', 'client_withdrawal')),
    qty NUMERIC(38,8) NOT NULL CHECK (qty > 0),
    sequence_no BIGINT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'executing', 'completed', 'canceled')
    ),
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reordered_at TIMESTAMPTZ,
    reordered_by BIGINT REFERENCES users(id),
    completed_at TIMESTAMPTZ,
    cancel_reason TEXT,
    CHECK ((reordered_at IS NULL) = (reordered_by IS NULL)),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL)),
    CHECK (status <> 'canceled' OR NULLIF(BTRIM(cancel_reason), '') IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_usdt_fulfillment_queue_active_deal
    ON usdt_fulfillment_queue(deal_id)
    WHERE status IN ('queued', 'executing');
CREATE INDEX IF NOT EXISTS idx_usdt_fulfillment_queue_order
    ON usdt_fulfillment_queue(sequence_no)
    WHERE status IN ('queued', 'executing');

-- USDT profit remains separate until the atomic capitalization job applies it.
CREATE TABLE IF NOT EXISTS profit_usdt_accruals (
    id BIGSERIAL PRIMARY KEY,
    deal_id BIGINT NOT NULL REFERENCES deals(id),
    qty NUMERIC(38,8) NOT NULL CHECK (qty > 0),
    valuation_rate NUMERIC(38,8),
    rub_value NUMERIC(38,8),
    quote_source TEXT,
    quote_observed_at TIMESTAMPTZ,
    valuation_actor_id BIGINT REFERENCES users(id),
    valuation_reason TEXT,
    capitalization_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        capitalization_status IN ('pending', 'capitalized')
    ),
    capitalized_at TIMESTAMPTZ,
    capitalization_move_id BIGINT REFERENCES firm_position_moves(id),
    settlement_status TEXT NOT NULL DEFAULT 'in_transit' CHECK (
        settlement_status IN ('in_transit', 'received')
    ),
    received_at TIMESTAMPTZ,
    payment_event_id BIGINT UNIQUE REFERENCES payment_watch_events(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (valuation_rate IS NULL AND rub_value IS NULL AND quote_source IS NULL
            AND quote_observed_at IS NULL AND valuation_actor_id IS NULL
            AND valuation_reason IS NULL)
        OR (valuation_rate > 0 AND rub_value IS NOT NULL
            AND NULLIF(BTRIM(quote_source), '') IS NOT NULL
            AND quote_observed_at IS NOT NULL
            AND (
                quote_source <> 'manual'
                OR (valuation_actor_id IS NOT NULL
                    AND NULLIF(BTRIM(valuation_reason), '') IS NOT NULL)
            ))
    ),
    CHECK (
        (capitalization_status = 'pending' AND capitalized_at IS NULL
            AND capitalization_move_id IS NULL)
        OR (capitalization_status = 'capitalized' AND valuation_rate IS NOT NULL
            AND capitalized_at IS NOT NULL AND capitalization_move_id IS NOT NULL)
    ),
    CHECK (
        (settlement_status = 'in_transit' AND received_at IS NULL)
        OR (settlement_status = 'received' AND received_at IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_profit_usdt_accruals_pending
    ON profit_usdt_accruals(created_at, id)
    WHERE capitalization_status = 'pending';
CREATE INDEX IF NOT EXISTS idx_profit_usdt_accruals_in_transit
    ON profit_usdt_accruals(created_at, id)
    WHERE settlement_status = 'in_transit';
CREATE INDEX IF NOT EXISTS idx_profit_usdt_accruals_capitalization_move
    ON profit_usdt_accruals(capitalization_move_id)
    WHERE capitalization_move_id IS NOT NULL;

-- A direct partner transfer watch claims at most one blockchain event.
CREATE TABLE IF NOT EXISTS partner_transfer_watches (
    id BIGSERIAL PRIMARY KEY,
    purchase_deal_id BIGINT NOT NULL REFERENCES deals(id),
    sale_deal_id BIGINT NOT NULL REFERENCES deals(id),
    network TEXT NOT NULL CHECK (NULLIF(BTRIM(network), '') IS NOT NULL),
    from_address TEXT NOT NULL CHECK (NULLIF(BTRIM(from_address), '') IS NOT NULL),
    to_address TEXT NOT NULL CHECK (NULLIF(BTRIM(to_address), '') IS NOT NULL),
    expected_qty NUMERIC(38,8) NOT NULL CHECK (expected_qty > 0),
    actual_qty NUMERIC(38,8),
    status TEXT NOT NULL DEFAULT 'watching' CHECK (
        status IN ('watching', 'matched', 'needs_review', 'stopped')
    ),
    tx_hash TEXT,
    event_index INTEGER,
    confirmed_at TIMESTAMPTZ,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stopped_at TIMESTAMPTZ,
    stop_reason TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    CHECK ((tx_hash IS NULL) = (event_index IS NULL)),
    CHECK ((tx_hash IS NULL) = (confirmed_at IS NULL)),
    CHECK (actual_qty IS NULL OR actual_qty > 0),
    CHECK (status = 'watching' OR status = 'stopped' OR tx_hash IS NOT NULL),
    CHECK (status NOT IN ('matched', 'needs_review') OR actual_qty IS NOT NULL),
    CHECK (status <> 'stopped' OR (
        stopped_at IS NOT NULL AND NULLIF(BTRIM(stop_reason), '') IS NOT NULL
    ))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_partner_transfer_watches_event
    ON partner_transfer_watches(UPPER(BTRIM(network)), LOWER(BTRIM(tx_hash)), event_index)
    WHERE tx_hash IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_partner_transfer_watches_active_sale
    ON partner_transfer_watches(sale_deal_id)
    WHERE status = 'watching';

-- A purchase lot may fund direct sales or increase the firm's wallet position.
CREATE TABLE IF NOT EXISTS partner_purchase_allocations (
    id BIGSERIAL PRIMARY KEY,
    purchase_deal_id BIGINT NOT NULL REFERENCES deals(id),
    sale_deal_id BIGINT REFERENCES deals(id),
    destination_kind TEXT NOT NULL CHECK (
        destination_kind IN ('firm_wallet', 'client_direct')
    ),
    qty NUMERIC(38,8) NOT NULL CHECK (qty > 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'settled', 'reversed')
    ),
    transfer_watch_id BIGINT REFERENCES partner_transfer_watches(id),
    position_move_id BIGINT UNIQUE REFERENCES firm_position_moves(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at TIMESTAMPTZ,
    reversed_at TIMESTAMPTZ,
    reversal_reason TEXT,
    CHECK (
        (destination_kind = 'firm_wallet' AND sale_deal_id IS NULL
            AND transfer_watch_id IS NULL)
        OR (destination_kind = 'client_direct' AND sale_deal_id IS NOT NULL
            AND transfer_watch_id IS NOT NULL AND position_move_id IS NULL)
    ),
    CHECK (status <> 'settled' OR settled_at IS NOT NULL),
    CHECK (status <> 'active' OR (settled_at IS NULL AND reversed_at IS NULL)),
    CHECK (status <> 'reversed' OR (
        reversed_at IS NOT NULL AND NULLIF(BTRIM(reversal_reason), '') IS NOT NULL
    ))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_partner_purchase_allocations_active_sale
    ON partner_purchase_allocations(sale_deal_id)
    WHERE sale_deal_id IS NOT NULL AND status = 'active';
CREATE INDEX IF NOT EXISTS idx_partner_purchase_allocations_purchase
    ON partner_purchase_allocations(purchase_deal_id, created_at, id);

CREATE OR REPLACE FUNCTION prevent_accounting_snapshot_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'firm_wallet_fact_snapshots is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_firm_wallet_fact_snapshots_append_only
    ON firm_wallet_fact_snapshots;
CREATE TRIGGER trg_firm_wallet_fact_snapshots_append_only
BEFORE UPDATE OR DELETE ON firm_wallet_fact_snapshots
FOR EACH ROW
EXECUTE FUNCTION prevent_accounting_snapshot_mutation();
