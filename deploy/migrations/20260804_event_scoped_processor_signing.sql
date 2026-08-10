BEGIN;

CREATE TABLE IF NOT EXISTS processor_identities (
    id SERIAL PRIMARY KEY,
    assignment_id VARCHAR(36) NOT NULL UNIQUE,
    instance_id VARCHAR(36) NOT NULL,
    entity_id VARCHAR(64) NOT NULL UNIQUE,
    event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    event_evidence_id VARCHAR(36) NOT NULL,
    event_display_name VARCHAR(128),
    display_label VARCHAR(128),
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','active','revoked')),
    active_key_id VARCHAR(19),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    CONSTRAINT uq_processor_identity_entity UNIQUE (instance_id, entity_id)
);
CREATE INDEX IF NOT EXISTS ix_processor_identities_event_id ON processor_identities(event_id);
CREATE INDEX IF NOT EXISTS ix_processor_identities_event_ref ON processor_identities(event_evidence_id);
CREATE INDEX IF NOT EXISTS ix_processor_identities_status ON processor_identities(status);

CREATE TABLE IF NOT EXISTS processor_policy_acknowledgements (
    id SERIAL PRIMARY KEY,
    acknowledgement_id VARCHAR(36) NOT NULL UNIQUE,
    instance_id VARCHAR(36) NOT NULL,
    event_evidence_id VARCHAR(36) NOT NULL,
    entity_id VARCHAR(64) NOT NULL,
    key_id VARCHAR(19) NOT NULL,
    policy_version INTEGER NOT NULL,
    policy_sha256 VARCHAR(64) NOT NULL,
    document_json TEXT NOT NULL,
    document_sha256 VARCHAR(64) NOT NULL UNIQUE,
    signature_sha256 VARCHAR(64) NOT NULL UNIQUE,
    acknowledged_at TIMESTAMPTZ NOT NULL,
    instance_record_sha256 VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_processor_policy_acknowledgement
        UNIQUE (event_evidence_id, entity_id, policy_version, policy_sha256)
);
CREATE INDEX IF NOT EXISTS ix_processor_policy_event ON processor_policy_acknowledgements(event_evidence_id);
CREATE INDEX IF NOT EXISTS ix_processor_policy_entity ON processor_policy_acknowledgements(entity_id);

ALTER TABLE evidence_key_registration_challenges
    ADD COLUMN IF NOT EXISTS event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS event_evidence_id VARCHAR(36),
    ADD COLUMN IF NOT EXISTS event_display_name VARCHAR(128),
    ADD COLUMN IF NOT EXISTS display_label VARCHAR(128);
CREATE INDEX IF NOT EXISTS ix_evidence_key_challenge_event_id ON evidence_key_registration_challenges(event_id);
CREATE INDEX IF NOT EXISTS ix_evidence_key_challenge_event_ref ON evidence_key_registration_challenges(event_evidence_id);

ALTER TABLE desktop_deletion_work_orders
    ADD COLUMN IF NOT EXISTS processor_entity_id VARCHAR(64) NOT NULL,
    ADD COLUMN IF NOT EXISTS processor_key_id VARCHAR(19) NOT NULL,
    ADD COLUMN IF NOT EXISTS report_signature_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS copy_resolution_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS copy_resolution_signature_sha256 VARCHAR(64);
ALTER TABLE desktop_deletion_work_orders DROP CONSTRAINT IF EXISTS uq_desktop_work_order_scope;
ALTER TABLE desktop_deletion_work_orders ADD CONSTRAINT uq_desktop_work_order_scope
    UNIQUE NULLS NOT DISTINCT (case_id, event_ref, subject_ref, processor_entity_id);
CREATE INDEX IF NOT EXISTS ix_desktop_work_orders_processor_entity
    ON desktop_deletion_work_orders(processor_entity_id);

CREATE TABLE IF NOT EXISTS deletion_required_processors (
    id SERIAL PRIMARY KEY,
    requirement_id VARCHAR(36) NOT NULL UNIQUE,
    case_id INTEGER NOT NULL REFERENCES deletion_cases(id) ON DELETE CASCADE,
    event_ref VARCHAR(36) NOT NULL,
    processor_entity_id VARCHAR(64) NOT NULL,
    snapshotted_key_id VARCHAR(19) NOT NULL,
    snapshotted_public_key_sha256 VARCHAR(64) NOT NULL,
    deletion_receipt_sha256 VARCHAR(64),
    copy_resolution_sha256 VARCHAR(64),
    completed_key_id VARCHAR(19),
    completed_public_key_sha256 VARCHAR(64),
    state VARCHAR(24) NOT NULL DEFAULT 'awaiting_desktop'
        CHECK (state IN ('awaiting_desktop','deletion_received','complete','blocked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_deletion_required_processor UNIQUE (case_id, event_ref, processor_entity_id)
);
CREATE INDEX IF NOT EXISTS ix_deletion_required_processors_case ON deletion_required_processors(case_id);
CREATE INDEX IF NOT EXISTS ix_deletion_required_processors_event ON deletion_required_processors(event_ref);
CREATE INDEX IF NOT EXISTS ix_deletion_required_processors_entity ON deletion_required_processors(processor_entity_id);

COMMIT;
