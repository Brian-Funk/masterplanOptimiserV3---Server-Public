BEGIN;

ALTER TABLE processor_policy_acknowledgements
    ADD COLUMN IF NOT EXISTS evidence_package_json TEXT,
    ADD COLUMN IF NOT EXISTS evidence_package_sha256 VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS uq_processor_policy_evidence_package
    ON processor_policy_acknowledgements(evidence_package_sha256)
    WHERE evidence_package_sha256 IS NOT NULL;

ALTER TABLE desktop_deletion_work_orders
    ADD COLUMN IF NOT EXISTS report_evidence_package_json TEXT,
    ADD COLUMN IF NOT EXISTS report_evidence_package_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS copy_resolution_evidence_package_json TEXT,
    ADD COLUMN IF NOT EXISTS copy_resolution_evidence_package_sha256 VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS uq_desktop_report_evidence_package
    ON desktop_deletion_work_orders(report_evidence_package_sha256)
    WHERE report_evidence_package_sha256 IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_desktop_copy_resolution_evidence_package
    ON desktop_deletion_work_orders(copy_resolution_evidence_package_sha256)
    WHERE copy_resolution_evidence_package_sha256 IS NOT NULL;

COMMIT;
