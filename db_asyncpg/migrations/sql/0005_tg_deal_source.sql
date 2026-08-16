BEGIN;

ALTER TABLE deals ADD COLUMN IF NOT EXISTS source_kind TEXT;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS source_ref TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_deals_source_ref
    ON deals(source, source_kind, source_ref)
    WHERE source_ref IS NOT NULL;

COMMIT;
