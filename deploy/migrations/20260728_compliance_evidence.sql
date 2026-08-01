BEGIN;

ALTER TABLE deletion_request_jobs
    ADD COLUMN IF NOT EXISTS request_type VARCHAR(32) NOT NULL DEFAULT 'full_erasure',
    ADD COLUMN IF NOT EXISTS identity_verification VARCHAR(48) NOT NULL DEFAULT 'recent_passkey_reauthentication',
    ADD COLUMN IF NOT EXISTS verification_method VARCHAR(48),
    ADD COLUMN IF NOT EXISTS response_sent_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS decision_code VARCHAR(64),
    ADD COLUMN IF NOT EXISTS request_manifest_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS acceptance_receipt_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS access_revocation_receipt_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS desktop_attestation_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS live_purge_receipt_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS privacy_action_id VARCHAR(36),
    ADD COLUMN IF NOT EXISTS privacy_action_sequence INTEGER,
    ADD COLUMN IF NOT EXISTS peer_confirmation_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS peer_confirmed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS replacement_package_id VARCHAR(36),
    ADD COLUMN IF NOT EXISTS replacement_package_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS backup_attestation_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS controller_approval_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS final_receipt_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS exception_codes_json VARCHAR(1024);

ALTER TABLE deletion_request_jobs DROP CONSTRAINT IF EXISTS ck_deletion_request_state;
ALTER TABLE deletion_request_jobs DROP CONSTRAINT IF EXISTS deletion_request_jobs_state_check;
ALTER TABLE deletion_request_jobs ADD CONSTRAINT ck_deletion_request_state CHECK (state IN (
    'submitted','under_review','accepted','rejected','withdrawn','access_revoked',
    'awaiting_desktop_attestation','ready_for_live_purge','live_purge_in_progress',
    'live_data_purged','peer_replication_pending','peer_replication_confirmed',
    'awaiting_clean_backup','clean_backup_verified','awaiting_backup_deletion_attestation',
    'ready_for_controller_approval','complete','complete_with_exceptions','failed'
));
CREATE UNIQUE INDEX IF NOT EXISTS uq_deletion_jobs_privacy_action
    ON deletion_request_jobs(privacy_action_id) WHERE privacy_action_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS evidence_keys (
    id SERIAL PRIMARY KEY,
    key_id VARCHAR(19) NOT NULL UNIQUE,
    public_key TEXT NOT NULL,
    public_key_sha256 VARCHAR(64) NOT NULL UNIQUE,
    role VARCHAR(32) NOT NULL CHECK (role IN (
        'instance','controller','root_operator','desktop_operator','backup_custodian'
    )),
    valid_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_evidence_keys_key_id ON evidence_keys(key_id);

CREATE TABLE IF NOT EXISTS evidence_key_registration_challenges (
    id SERIAL PRIMARY KEY,
    challenge_id VARCHAR(36) NOT NULL UNIQUE,
    public_key TEXT NOT NULL,
    public_key_sha256 VARCHAR(64) NOT NULL,
    role VARCHAR(32) NOT NULL,
    challenge_sha256 VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS evidence_chain_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    instance_id VARCHAR(36) NOT NULL UNIQUE,
    chain_id VARCHAR(36) NOT NULL UNIQUE,
    evidence_mode VARCHAR(16) NOT NULL CHECK (evidence_mode IN ('required','advisory','disabled')),
    last_sequence INTEGER NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    head_sha256 VARCHAR(64),
    initialised_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verified_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS evidence_operations (
    id SERIAL PRIMARY KEY,
    operation_id VARCHAR(36) NOT NULL UNIQUE,
    record_id VARCHAR(36) NOT NULL UNIQUE,
    workflow_type VARCHAR(32) NOT NULL,
    workflow_id VARCHAR(36) NOT NULL,
    operation_type VARCHAR(64) NOT NULL,
    record_type VARCHAR(64),
    payload_json TEXT NOT NULL,
    management_audit_tail_sha256 VARCHAR(64),
    state VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','appended','complete','failed')),
    record_sha256 VARCHAR(64),
    error_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_evidence_operation UNIQUE (workflow_type, workflow_id, operation_type)
);
CREATE INDEX IF NOT EXISTS ix_evidence_operations_workflow ON evidence_operations(workflow_id);
CREATE INDEX IF NOT EXISTS ix_evidence_operations_state ON evidence_operations(state);

ALTER TABLE evidence_operations
    ADD COLUMN IF NOT EXISTS record_type VARCHAR(64),
    ADD COLUMN IF NOT EXISTS management_audit_tail_sha256 VARCHAR(64);

CREATE TABLE IF NOT EXISTS event_purge_jobs (
    id SERIAL PRIMARY KEY,
    purge_id VARCHAR(36) NOT NULL UNIQUE,
    instance_id VARCHAR(36) NOT NULL,
    event_evidence_id VARCHAR(36) NOT NULL,
    event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    state VARCHAR(48) NOT NULL DEFAULT 'created' CHECK (state IN (
        'created','awaiting_desktop_attestation','ready_for_live_purge','live_purge_in_progress',
        'live_data_purged','peer_replication_pending','peer_replication_confirmed',
        'awaiting_clean_backup','clean_backup_verified','awaiting_backup_deletion_attestation',
        'ready_for_controller_approval','complete','complete_with_exceptions','failed'
    )),
    initiation_sha256 VARCHAR(64),
    desktop_attestation_sha256 VARCHAR(64),
    live_purge_receipt_sha256 VARCHAR(64),
    privacy_action_id VARCHAR(36) UNIQUE,
    peer_confirmation_sha256 VARCHAR(64),
    replacement_package_id VARCHAR(36),
    replacement_package_sha256 VARCHAR(64),
    backup_attestation_sha256 VARCHAR(64),
    controller_approval_sha256 VARCHAR(64),
    final_receipt_sha256 VARCHAR(64),
    exception_codes_json VARCHAR(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    live_data_purged_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_event_purge_jobs_state ON event_purge_jobs(state);
CREATE INDEX IF NOT EXISTS ix_event_purge_jobs_event ON event_purge_jobs(event_evidence_id);

CREATE TABLE IF NOT EXISTS evidence_attestation_references (
    id SERIAL PRIMARY KEY,
    attestation_id VARCHAR(36) NOT NULL UNIQUE,
    attestation_type VARCHAR(64) NOT NULL,
    workflow_type VARCHAR(32) NOT NULL,
    workflow_id VARCHAR(36) NOT NULL,
    signer_key_id VARCHAR(19) NOT NULL,
    manifest_sha256 VARCHAR(64) NOT NULL UNIQUE,
    outcome VARCHAR(32) NOT NULL DEFAULT 'operator_attested',
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_evidence_attestations_workflow
    ON evidence_attestation_references(workflow_id);

CREATE TABLE IF NOT EXISTS backup_inventory_records (
    id SERIAL PRIMARY KEY,
    package_id VARCHAR(36) NOT NULL UNIQUE,
    package_sha256 VARCHAR(64) NOT NULL,
    archive_sha256 VARCHAR(64),
    recovery_key_id VARCHAR(19),
    status VARCHAR(40) NOT NULL DEFAULT 'active' CHECK (status IN (
        'active','superseded_pending_deletion','attested_deleted','expired','unknown_legacy'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verified_at TIMESTAMPTZ,
    confirmed_at TIMESTAMPTZ,
    delete_after TIMESTAMPTZ,
    replacement_package_id VARCHAR(36),
    deletion_attestation_sha256 VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS ix_backup_inventory_status ON backup_inventory_records(status);

CREATE TABLE IF NOT EXISTS privacy_action_receipts (
    id SERIAL PRIMARY KEY,
    privacy_action_id VARCHAR(36) NOT NULL UNIQUE,
    sequence INTEGER NOT NULL UNIQUE CHECK (sequence >= 1),
    instance_id VARCHAR(36) NOT NULL,
    event_ref VARCHAR(36) NOT NULL,
    subject_ref VARCHAR(36),
    action_type VARCHAR(24) NOT NULL CHECK (action_type IN ('subject_delete','event_delete')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    local_applied_at TIMESTAMPTZ,
    peer_confirmed_at TIMESTAMPTZ,
    retain_until TIMESTAMPTZ NOT NULL,
    witness_receipt_sha256 VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS ix_privacy_actions_event ON privacy_action_receipts(event_ref);
CREATE INDEX IF NOT EXISTS ix_privacy_actions_subject ON privacy_action_receipts(subject_ref);

COMMIT;
