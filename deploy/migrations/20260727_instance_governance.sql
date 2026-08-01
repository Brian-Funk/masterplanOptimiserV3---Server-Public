BEGIN;

CREATE TABLE IF NOT EXISTS instance_governance_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    instance_id VARCHAR(36) NOT NULL UNIQUE,
    controller_type VARCHAR(24) NOT NULL CHECK (controller_type IN ('organisation', 'individual')),
    controller_legal_name VARCHAR(200) NOT NULL,
    controller_postal_address VARCHAR(500) NOT NULL,
    controller_country VARCHAR(2) NOT NULL,
    privacy_contact_email VARCHAR(320) NOT NULL,
    privacy_contact_phone VARCHAR(64),
    dpo_contact VARCHAR(320),
    supervisory_authority_name VARCHAR(200) NOT NULL,
    supervisory_authority_url VARCHAR(500) NOT NULL,
    default_locale VARCHAR(16) NOT NULL DEFAULT 'en',
    processor_summary TEXT NOT NULL,
    retention_summary TEXT NOT NULL,
    rights_summary TEXT NOT NULL,
    terms_summary TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS governance_publications (
    id SERIAL PRIMARY KEY,
    version INTEGER NOT NULL UNIQUE CHECK (version >= 1),
    content_json TEXT NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL UNIQUE,
    published_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_policy_acknowledgements (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
    policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
    scope VARCHAR(48) NOT NULL CHECK (scope IN (
        'instance_root', 'event_creator', 'head_organiser',
        'authorised_editor', 'field_visibility_administrator'
    )),
    acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    superseded_at TIMESTAMPTZ,
    CONSTRAINT uq_data_policy_acknowledgement
        UNIQUE (user_id, event_id, policy_version, scope)
);

CREATE INDEX IF NOT EXISTS ix_data_policy_ack_user ON data_policy_acknowledgements(user_id);
CREATE INDEX IF NOT EXISTS ix_data_policy_ack_event ON data_policy_acknowledgements(event_id);

ALTER TABLE events ADD COLUMN IF NOT EXISTS evidence_id VARCHAR(36);
UPDATE events SET evidence_id = gen_random_uuid()::text WHERE evidence_id IS NULL;
ALTER TABLE events ALTER COLUMN evidence_id SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_events_evidence_id ON events(evidence_id);

ALTER TABLE users ADD COLUMN IF NOT EXISTS evidence_subject_id VARCHAR(36);
UPDATE users SET evidence_subject_id = gen_random_uuid()::text WHERE evidence_subject_id IS NULL;
ALTER TABLE users ALTER COLUMN evidence_subject_id SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_evidence_subject_id ON users(evidence_subject_id);

ALTER TABLE published_persons ADD COLUMN IF NOT EXISTS evidence_subject_id VARCHAR(36);
UPDATE published_persons SET evidence_subject_id = gen_random_uuid()::text WHERE evidence_subject_id IS NULL;
ALTER TABLE published_persons ALTER COLUMN evidence_subject_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS ix_published_persons_evidence_subject_id ON published_persons(evidence_subject_id);

CREATE TABLE IF NOT EXISTS deletion_request_jobs (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(36) NOT NULL UNIQUE,
    instance_id VARCHAR(36) NOT NULL,
    event_evidence_id VARCHAR(36) NOT NULL,
    subject_evidence_id VARCHAR(36) NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    state VARCHAR(48) NOT NULL DEFAULT 'submitted' CHECK (state IN (
        'submitted','under_review','accepted','rejected','withdrawn','access_revoked',
        'awaiting_desktop_attestation','ready_for_live_purge','live_purge_in_progress',
        'live_data_purged','peer_replication_pending','peer_replication_confirmed',
        'awaiting_clean_backup','clean_backup_verified','awaiting_backup_deletion_attestation',
        'ready_for_controller_approval','complete','failed'
    )),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    normal_response_due_at TIMESTAMPTZ NOT NULL,
    decision_at TIMESTAMPTZ,
    access_revoked_at TIMESTAMPTZ,
    live_data_purged_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_deletion_jobs_request_id ON deletion_request_jobs(request_id);
CREATE INDEX IF NOT EXISTS ix_deletion_jobs_instance ON deletion_request_jobs(instance_id);
CREATE INDEX IF NOT EXISTS ix_deletion_jobs_subject ON deletion_request_jobs(subject_evidence_id);
CREATE INDEX IF NOT EXISTS ix_deletion_jobs_event ON deletion_request_jobs(event_evidence_id);
CREATE INDEX IF NOT EXISTS ix_deletion_jobs_state ON deletion_request_jobs(state);

COMMIT;
