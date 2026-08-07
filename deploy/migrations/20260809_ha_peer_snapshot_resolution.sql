BEGIN;

ALTER TABLE deletion_cases
    ADD COLUMN IF NOT EXISTS peer_backup_resolution_id VARCHAR(36),
    ADD COLUMN IF NOT EXISTS peer_backup_resolution_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS peer_backup_resolved_at TIMESTAMPTZ;

COMMIT;
