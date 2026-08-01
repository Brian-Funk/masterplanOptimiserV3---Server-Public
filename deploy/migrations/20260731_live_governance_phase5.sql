BEGIN;

ALTER TABLE instance_governance_profile
    ADD COLUMN IF NOT EXISTS structured_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE governance_publications
    ADD COLUMN IF NOT EXISTS supersedes_version INTEGER,
    ADD COLUMN IF NOT EXISTS material_change BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS change_summary_json TEXT NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS source_json TEXT NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS source_sha256 VARCHAR(64) NOT NULL DEFAULT '0000000000000000000000000000000000000000000000000000000000000000';

ALTER TABLE governance_publications
    DROP CONSTRAINT IF EXISTS uq_governance_publication_hash,
    DROP CONSTRAINT IF EXISTS governance_publications_content_sha256_key;

CREATE TABLE IF NOT EXISTS event_governance_overrides (
    event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    controller_override_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    controller_identity_override VARCHAR(200),
    privacy_contact_override VARCHAR(320),
    retention_override_days INTEGER CHECK (
        retention_override_days IS NULL OR retention_override_days BETWEEN 1 AND 3650
    ),
    enabled_optional_features_json TEXT NOT NULL DEFAULT '[]',
    policy_version INTEGER NOT NULL,
    updated_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMIT;
