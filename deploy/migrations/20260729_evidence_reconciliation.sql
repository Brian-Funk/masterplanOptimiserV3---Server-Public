BEGIN;

ALTER TABLE evidence_operations
    ADD COLUMN IF NOT EXISTS record_type VARCHAR(64),
    ADD COLUMN IF NOT EXISTS management_audit_tail_sha256 VARCHAR(64);

ALTER TABLE deletion_request_jobs
    ADD COLUMN IF NOT EXISTS peer_replication_job_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS peer_bundle_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS peer_bundle_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS peer_generation INTEGER,
    ADD COLUMN IF NOT EXISTS peer_accepted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS clean_backup_job_id VARCHAR(36),
    ADD COLUMN IF NOT EXISTS clean_backup_receipt_id VARCHAR(36);

ALTER TABLE event_purge_jobs
    ADD COLUMN IF NOT EXISTS peer_replication_job_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS peer_bundle_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS peer_bundle_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS peer_generation INTEGER,
    ADD COLUMN IF NOT EXISTS peer_accepted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS clean_backup_job_id VARCHAR(36),
    ADD COLUMN IF NOT EXISTS clean_backup_receipt_id VARCHAR(36);

COMMIT;
