BEGIN;

CREATE TABLE IF NOT EXISTS evidence_archive_submissions (
    id SERIAL PRIMARY KEY,
    submission_id VARCHAR(40) NOT NULL UNIQUE,
    repository_id VARCHAR(128) NOT NULL,
    controller_id VARCHAR(20) NOT NULL,
    instance_id VARCHAR(36) NOT NULL,
    bundle_id VARCHAR(36) NOT NULL,
    bundle_sha256 VARCHAR(64) NOT NULL UNIQUE,
    chain_head_sha256 VARCHAR(64) NOT NULL,
    bundle_path TEXT NOT NULL,
    state VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ,
    lease_owner VARCHAR(64),
    lease_expires_at TIMESTAMPTZ,
    failure_reason VARCHAR(64),
    branch_name VARCHAR(180),
    base_sha VARCHAR(64),
    pull_request_number INTEGER,
    pull_request_head_sha VARCHAR(64),
    merge_commit_sha VARCHAR(64),
    archive_record_sha256 VARCHAR(64),
    checks_started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT ck_evidence_archive_state CHECK (
        state IN ('pending','verifying','uploading','awaiting_checks','awaiting_merge',
                  'verified','failed','blocked','requires_controller_action')
    ),
    CONSTRAINT uq_evidence_archive_chain_head UNIQUE (repository_id, instance_id, chain_head_sha256)
);

CREATE INDEX IF NOT EXISTS ix_evidence_archive_submission_id ON evidence_archive_submissions (submission_id);
CREATE INDEX IF NOT EXISTS ix_evidence_archive_repository_id ON evidence_archive_submissions (repository_id);
CREATE INDEX IF NOT EXISTS ix_evidence_archive_instance_id ON evidence_archive_submissions (instance_id);
CREATE INDEX IF NOT EXISTS ix_evidence_archive_state ON evidence_archive_submissions (state);
CREATE INDEX IF NOT EXISTS ix_evidence_archive_next_attempt ON evidence_archive_submissions (next_attempt_at);
CREATE INDEX IF NOT EXISTS ix_evidence_archive_lease_expires ON evidence_archive_submissions (lease_expires_at);

COMMIT;
