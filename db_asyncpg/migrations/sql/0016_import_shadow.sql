ALTER TABLE expenses
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_expenses_idempotency
    ON expenses(idempotency_key) WHERE idempotency_key IS NOT NULL;

ALTER TABLE capital_moves
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_capital_moves_idempotency
    ON capital_moves(idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS accounting_import_runs (
    id BIGSERIAL PRIMARY KEY,
    source_name TEXT NOT NULL CHECK (NULLIF(BTRIM(source_name), '') IS NOT NULL),
    manifest_checksum TEXT NOT NULL CHECK (NULLIF(BTRIM(manifest_checksum), '') IS NOT NULL),
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    checkpoint INTEGER NOT NULL DEFAULT 0 CHECK (checkpoint >= 0),
    record_count INTEGER NOT NULL CHECK (record_count >= 0),
    strategy JSONB NOT NULL DEFAULT '{}'::jsonb,
    control_totals JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_kind TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_name, manifest_checksum),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS accounting_import_records (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES accounting_import_runs(id),
    source_name TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    target_table TEXT NOT NULL,
    target_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_name, entity_kind, source_key),
    UNIQUE (run_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS dashboard_shadow_reports (
    id BIGSERIAL PRIMARY KEY,
    business_date DATE NOT NULL,
    primary_source TEXT NOT NULL CHECK (primary_source IN ('sheets', 'postgres')),
    status TEXT NOT NULL CHECK (status IN ('matched', 'mismatched')),
    compared_fields INTEGER NOT NULL CHECK (compared_fields >= 0),
    mismatch_count INTEGER NOT NULL CHECK (mismatch_count >= 0),
    absolute_tolerance NUMERIC(38,8) NOT NULL CHECK (absolute_tolerance >= 0),
    relative_tolerance NUMERIC(38,8) NOT NULL CHECK (relative_tolerance >= 0),
    diagnostics JSONB NOT NULL DEFAULT '[]'::jsonb,
    sheets_data_as_of TIMESTAMPTZ NOT NULL,
    db_data_as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dashboard_shadow_reports_date
    ON dashboard_shadow_reports(business_date, id DESC);
