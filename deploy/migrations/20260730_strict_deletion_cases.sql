-- Current-schema reset for deletion accountability.
--
-- This installation has no server data requiring legacy migration. The old
-- split subject/event jobs and imported-attestation state are intentionally
-- discarded instead of translated into claims the new workflow cannot prove.

BEGIN;

DROP TABLE IF EXISTS deletion_approval_challenges CASCADE;
DROP TABLE IF EXISTS deletion_checklist_approvals CASCADE;
DROP TABLE IF EXISTS desktop_deletion_work_orders CASCADE;
DROP TABLE IF EXISTS deletion_subject_scopes CASCADE;
DROP TABLE IF EXISTS event_purge_jobs CASCADE;
DROP TABLE IF EXISTS evidence_attestation_references CASCADE;
DROP TABLE IF EXISTS evidence_key_registration_challenges CASCADE;
DROP TABLE IF EXISTS deletion_request_jobs CASCADE;
DROP TABLE IF EXISTS deletion_cases CASCADE;

CREATE TABLE deletion_cases (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(36) NOT NULL UNIQUE,
    case_type VARCHAR(32) NOT NULL CHECK (
        case_type IN ('personal_data_erasure','event_erasure')
    ),
    instance_id VARCHAR(36) NOT NULL,
    event_evidence_id VARCHAR(36) NOT NULL,
    subject_evidence_id VARCHAR(36) NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    request_type VARCHAR(32) NOT NULL DEFAULT 'full_erasure',
    identity_verification VARCHAR(48) NOT NULL DEFAULT 'recent_passkey_reauthentication',
    verification_method VARCHAR(48),
    state VARCHAR(48) NOT NULL DEFAULT 'submitted' CHECK (state IN (
        'submitted','under_review','accepted','rejected','withdrawn','access_revoked',
        'awaiting_desktop_report','ready_for_live_purge','live_purge_in_progress',
        'live_data_purged','peer_replication_pending','peer_replication_confirmed',
        'awaiting_clean_backup','clean_backup_verified','awaiting_backup_resolution',
        'restricted_retention','awaiting_checklist','awaiting_approvals',
        'ready_for_completion','complete','failed'
    )),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    normal_response_due_at TIMESTAMPTZ NOT NULL,
    decision_at TIMESTAMPTZ,
    response_sent_at TIMESTAMPTZ,
    decision_code VARCHAR(64),
    access_revoked_at TIMESTAMPTZ,
    request_manifest_sha256 VARCHAR(64),
    acceptance_receipt_sha256 VARCHAR(64),
    access_revocation_receipt_sha256 VARCHAR(64),
    desktop_report_sha256 VARCHAR(64),
    desktop_deletion_required BOOLEAN NOT NULL DEFAULT TRUE,
    live_data_purged_at TIMESTAMPTZ,
    live_purge_receipt_sha256 VARCHAR(64),
    privacy_action_id VARCHAR(36) UNIQUE,
    privacy_action_sequence INTEGER,
    peer_confirmation_sha256 VARCHAR(64),
    peer_confirmed_at TIMESTAMPTZ,
    peer_replication_job_id VARCHAR(128),
    peer_bundle_id VARCHAR(128),
    peer_bundle_sha256 VARCHAR(64),
    peer_generation INTEGER,
    peer_accepted_at TIMESTAMPTZ,
    replacement_package_id VARCHAR(36),
    replacement_package_sha256 VARCHAR(64),
    clean_backup_job_id VARCHAR(36),
    clean_backup_receipt_id VARCHAR(36),
    backup_resolution_sha256 VARCHAR(64),
    checklist_version INTEGER,
    checklist_json TEXT,
    checklist_sha256 VARCHAR(64) UNIQUE,
    checklist_created_at TIMESTAMPTZ,
    processor_approval_required BOOLEAN NOT NULL DEFAULT FALSE,
    executor_approval_sha256 VARCHAR(64),
    controller_approval_sha256 VARCHAR(64),
    processor_approval_sha256 VARCHAR(64),
    status_capability_sha256 VARCHAR(64) UNIQUE,
    status_capability_expires_at TIMESTAMPTZ,
    retention_reason_code VARCHAR(64),
    retention_review_at TIMESTAMPTZ,
    outstanding_actions_json TEXT,
    final_receipt_sha256 VARCHAR(64),
    completed_at TIMESTAMPTZ,
    error_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_deletion_cases_request_id ON deletion_cases(request_id);
CREATE INDEX ix_deletion_cases_instance ON deletion_cases(instance_id);
CREATE INDEX ix_deletion_cases_event ON deletion_cases(event_evidence_id);
CREATE INDEX ix_deletion_cases_subject ON deletion_cases(subject_evidence_id);
CREATE INDEX ix_deletion_cases_state ON deletion_cases(state);
CREATE INDEX ix_deletion_cases_type ON deletion_cases(case_type);
CREATE INDEX ix_deletion_cases_user ON deletion_cases(user_id);

CREATE TABLE deletion_subject_scopes (
    id SERIAL PRIMARY KEY,
    scope_id VARCHAR(36) NOT NULL UNIQUE,
    case_id INTEGER NOT NULL REFERENCES deletion_cases(id) ON DELETE CASCADE,
    event_ref VARCHAR(36) NOT NULL,
    subject_ref VARCHAR(36),
    event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    state VARCHAR(24) NOT NULL DEFAULT 'pending' CHECK (
        state IN ('pending','desktop_deleted','server_deleted','complete','failed')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_deletion_scope UNIQUE NULLS NOT DISTINCT (
        case_id, event_ref, subject_ref
    )
);

CREATE TABLE desktop_deletion_work_orders (
    id SERIAL PRIMARY KEY,
    work_order_id VARCHAR(36) NOT NULL UNIQUE,
    case_id INTEGER NOT NULL REFERENCES deletion_cases(id) ON DELETE CASCADE,
    event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    event_ref VARCHAR(36) NOT NULL,
    subject_ref VARCHAR(36),
    operation VARCHAR(24) NOT NULL CHECK (
        operation IN ('delete_subject','delete_event')
    ),
    state VARCHAR(24) NOT NULL DEFAULT 'open' CHECK (
        state IN ('open','claimed','report_received','cancelled','failed')
    ),
    claim_capability_sha256 VARCHAR(64) UNIQUE,
    claim_expires_at TIMESTAMPTZ,
    claimed_at TIMESTAMPTZ,
    report_json TEXT,
    report_sha256 VARCHAR(64) UNIQUE,
    reported_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_desktop_work_order_scope UNIQUE NULLS NOT DISTINCT (
        case_id, event_ref, subject_ref
    )
);
CREATE INDEX ix_desktop_deletion_work_orders_event ON desktop_deletion_work_orders(event_id);
CREATE INDEX ix_desktop_deletion_work_orders_state ON desktop_deletion_work_orders(state);

CREATE TABLE deletion_checklist_approvals (
    id SERIAL PRIMARY KEY,
    approval_id VARCHAR(36) NOT NULL UNIQUE,
    case_id INTEGER NOT NULL REFERENCES deletion_cases(id) ON DELETE CASCADE,
    checklist_sha256 VARCHAR(64) NOT NULL,
    role VARCHAR(24) NOT NULL CHECK (role IN ('executor','controller','processor')),
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    credential_sha256 VARCHAR(64) NOT NULL,
    approval_sha256 VARCHAR(64) NOT NULL UNIQUE,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_deletion_approval_role UNIQUE (case_id, checklist_sha256, role)
);

CREATE TABLE deletion_approval_challenges (
    id SERIAL PRIMARY KEY,
    challenge_id VARCHAR(36) NOT NULL UNIQUE,
    ceremony_id VARCHAR(64) NOT NULL UNIQUE
        REFERENCES passkey_ceremonies(id) ON DELETE CASCADE,
    case_id INTEGER NOT NULL REFERENCES deletion_cases(id) ON DELETE CASCADE,
    checklist_sha256 VARCHAR(64) NOT NULL,
    role VARCHAR(24) NOT NULL CHECK (role IN ('executor','controller','processor')),
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    consumed_at TIMESTAMPTZ
);

DROP TABLE IF EXISTS backup_inventory_records CASCADE;
CREATE TABLE backup_inventory_records (
    id SERIAL PRIMARY KEY,
    package_id VARCHAR(36) NOT NULL UNIQUE,
    package_sha256 VARCHAR(64) NOT NULL,
    archive_sha256 VARCHAR(64),
    recovery_key_id VARCHAR(19),
    status VARCHAR(40) NOT NULL DEFAULT 'active' CHECK (status IN (
        'active','superseded_pending_deletion','confirmed_deleted','expired'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verified_at TIMESTAMPTZ,
    confirmed_at TIMESTAMPTZ,
    delete_after TIMESTAMPTZ,
    replacement_package_id VARCHAR(36),
    deletion_resolution_sha256 VARCHAR(64)
);
CREATE INDEX ix_backup_inventory_package_id ON backup_inventory_records(package_id);
CREATE INDEX ix_backup_inventory_status ON backup_inventory_records(status);

DELETE FROM evidence_keys WHERE role <> 'instance';
ALTER TABLE evidence_keys DROP CONSTRAINT IF EXISTS ck_evidence_key_role;
ALTER TABLE evidence_keys DROP CONSTRAINT IF EXISTS evidence_keys_role_check;
ALTER TABLE evidence_keys ADD CONSTRAINT ck_evidence_key_role CHECK (role = 'instance');

ALTER TABLE evidence_chain_state
    DROP CONSTRAINT IF EXISTS ck_evidence_mode;
ALTER TABLE evidence_chain_state
    DROP CONSTRAINT IF EXISTS evidence_chain_state_evidence_mode_check;
UPDATE evidence_chain_state SET evidence_mode = 'required';
ALTER TABLE evidence_chain_state
    ALTER COLUMN evidence_mode SET DEFAULT 'required';
ALTER TABLE evidence_chain_state
    ADD CONSTRAINT ck_evidence_mode CHECK (evidence_mode = 'required');

COMMIT;
