BEGIN;

CREATE TABLE IF NOT EXISTS account_processing_consents (
    id SERIAL PRIMARY KEY,
    consent_id VARCHAR(36) NOT NULL UNIQUE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    user_subject_id VARCHAR(36) NOT NULL UNIQUE,
    event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    event_evidence_id VARCHAR(36),
    activation_link_id INTEGER REFERENCES activation_links(id) ON DELETE SET NULL,
    policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
    policy_sha256 VARCHAR(64) NOT NULL CHECK (policy_sha256 ~ '^[0-9a-f]{64}$'),
    statement_version VARCHAR(64) NOT NULL,
    statement_sha256 VARCHAR(64) NOT NULL CHECK (statement_sha256 ~ '^[0-9a-f]{64}$'),
    controller_identity VARCHAR(200) NOT NULL,
    document_json TEXT NOT NULL,
    instance_record_sha256 VARCHAR(64),
    consented_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_account_processing_consent_activation UNIQUE (activation_link_id)
);

CREATE INDEX IF NOT EXISTS ix_account_processing_consents_user
    ON account_processing_consents(user_id);
CREATE INDEX IF NOT EXISTS ix_account_processing_consents_subject
    ON account_processing_consents(user_subject_id);
CREATE INDEX IF NOT EXISTS ix_account_processing_consents_event
    ON account_processing_consents(event_id);
CREATE INDEX IF NOT EXISTS ix_account_processing_consents_event_evidence
    ON account_processing_consents(event_evidence_id);

COMMIT;
