ALTER TABLE dashboard_shadow_reports
    ADD COLUMN IF NOT EXISTS report_fingerprint TEXT;

UPDATE dashboard_shadow_reports
SET report_fingerprint = MD5(
    JSONB_BUILD_OBJECT(
        'status', status,
        'comparedFields', compared_fields,
        'mismatchCount', mismatch_count,
        'absoluteTolerance', absolute_tolerance,
        'relativeTolerance', relative_tolerance,
        'diagnostics', diagnostics
    )::TEXT
)
WHERE report_fingerprint IS NULL;

WITH duplicate_reports AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY business_date, primary_source, report_fingerprint
               ORDER BY id
           ) AS duplicate_number
    FROM dashboard_shadow_reports
)
DELETE FROM dashboard_shadow_reports report
USING duplicate_reports duplicate
WHERE report.id = duplicate.id
  AND duplicate.duplicate_number > 1;

ALTER TABLE dashboard_shadow_reports
    ALTER COLUMN report_fingerprint SET NOT NULL;

ALTER TABLE dashboard_shadow_reports
    ADD CONSTRAINT ck_dashboard_shadow_reports_fingerprint
    CHECK (report_fingerprint ~ '^[0-9a-f]{32}$');

CREATE UNIQUE INDEX IF NOT EXISTS uq_dashboard_shadow_reports_content
    ON dashboard_shadow_reports(business_date, primary_source, report_fingerprint);
