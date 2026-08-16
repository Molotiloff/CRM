BEGIN;

ALTER TABLE tg_outbox DROP CONSTRAINT IF EXISTS tg_outbox_status_check;
ALTER TABLE tg_outbox
    ADD CONSTRAINT tg_outbox_status_check
    CHECK (status IN ('pending','processing','sent','failed'));

ALTER TABLE tg_outbox ADD COLUMN IF NOT EXISTS dedup_key TEXT;
ALTER TABLE tg_outbox ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE tg_outbox ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;
ALTER TABLE tg_outbox ADD COLUMN IF NOT EXISTS last_error TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tg_outbox_dedup_key
    ON tg_outbox(dedup_key) WHERE dedup_key IS NOT NULL;
DROP INDEX IF EXISTS idx_tg_outbox_pending;
CREATE INDEX IF NOT EXISTS idx_tg_outbox_pending
    ON tg_outbox(available_at, id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_tg_outbox_processing
    ON tg_outbox(locked_at, id) WHERE status = 'processing';

COMMIT;
