BEGIN;

ALTER TABLE evidence_keys
    ADD COLUMN IF NOT EXISTS revocation_reason VARCHAR(32),
    ADD COLUMN IF NOT EXISTS supersedes_key_id VARCHAR(19),
    ADD COLUMN IF NOT EXISTS superseded_by_key_id VARCHAR(19),
    ADD COLUMN IF NOT EXISTS registration_proof_sha256 VARCHAR(64);

ALTER TABLE evidence_keys DROP CONSTRAINT IF EXISTS ck_evidence_key_role;
ALTER TABLE evidence_keys DROP CONSTRAINT IF EXISTS evidence_keys_role_check;
ALTER TABLE evidence_keys ADD CONSTRAINT ck_evidence_key_role CHECK (role IN (
    'instance','controller','root_operator','desktop_operator',
    'backup_custodian','evidence_auditor','processor'
));

ALTER TABLE evidence_keys DROP CONSTRAINT IF EXISTS ck_evidence_key_revocation_reason;
ALTER TABLE evidence_keys ADD CONSTRAINT ck_evidence_key_revocation_reason CHECK (
    revocation_reason IS NULL OR revocation_reason IN (
        'retired','lost','compromised','role_changed'
    )
);

CREATE INDEX IF NOT EXISTS ix_evidence_keys_supersedes_key_id
    ON evidence_keys(supersedes_key_id);

CREATE TABLE IF NOT EXISTS evidence_key_registration_challenges (
    id SERIAL PRIMARY KEY,
    challenge_id VARCHAR(36) NOT NULL UNIQUE,
    purpose VARCHAR(16) NOT NULL CHECK (purpose IN ('register','rotate')),
    public_key TEXT NOT NULL,
    public_key_sha256 VARCHAR(64) NOT NULL,
    key_id VARCHAR(19) NOT NULL,
    role VARCHAR(32) NOT NULL CHECK (role IN (
        'controller','root_operator','desktop_operator',
        'backup_custodian','evidence_auditor'
    )),
    supersedes_key_id VARCHAR(19),
    rotation_reason VARCHAR(32) CHECK (
        rotation_reason IS NULL OR rotation_reason IN ('routine','lost','compromised')
    ),
    challenge_json TEXT NOT NULL,
    challenge_sha256 VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_evidence_key_challenges_key_id
    ON evidence_key_registration_challenges(key_id);
CREATE INDEX IF NOT EXISTS ix_evidence_key_challenges_expires_at
    ON evidence_key_registration_challenges(expires_at);

COMMIT;
