BEGIN;

-- Preserve superseded generic public metadata for historic inspection, but do
-- not leave it active in the typed trust-key registry. No private material is
-- present in either table.
CREATE TABLE IF NOT EXISTS legacy_operator_evidence_keys
    (LIKE evidence_keys INCLUDING ALL);
INSERT INTO legacy_operator_evidence_keys
SELECT * FROM evidence_keys WHERE role <> 'instance'
ON CONFLICT DO NOTHING;
DELETE FROM evidence_keys WHERE role <> 'instance';
DELETE FROM evidence_key_registration_challenges;

ALTER TABLE evidence_keys
    ADD COLUMN IF NOT EXISTS instance_id VARCHAR(36),
    ADD COLUMN IF NOT EXISTS entity_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS algorithm VARCHAR(16) NOT NULL DEFAULT 'Ed25519',
    ADD COLUMN IF NOT EXISTS root_credential_id_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS root_action_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS trust_declaration_sha256 VARCHAR(64);

UPDATE evidence_keys
SET instance_id = (SELECT instance_id FROM evidence_chain_state WHERE id = 1),
    activated_at = COALESCE(activated_at, registered_at)
WHERE role = 'instance';

ALTER TABLE evidence_keys ALTER COLUMN instance_id SET NOT NULL;
ALTER TABLE evidence_keys DROP CONSTRAINT IF EXISTS ck_evidence_key_role;
ALTER TABLE evidence_keys DROP CONSTRAINT IF EXISTS evidence_keys_role_check;
ALTER TABLE evidence_keys ADD CONSTRAINT ck_evidence_key_role
    CHECK (role IN ('instance','controller','processor'));
CREATE INDEX IF NOT EXISTS ix_evidence_keys_instance_id ON evidence_keys(instance_id);
CREATE INDEX IF NOT EXISTS ix_evidence_keys_entity_id ON evidence_keys(entity_id);

ALTER TABLE evidence_key_registration_challenges
    ADD COLUMN IF NOT EXISTS instance_id VARCHAR(36),
    ADD COLUMN IF NOT EXISTS entity_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS action_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS possession_proof_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS previous_proof_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS root_ceremony_id VARCHAR(64);
ALTER TABLE evidence_key_registration_challenges DROP CONSTRAINT IF EXISTS ck_evidence_key_challenge_role;
ALTER TABLE evidence_key_registration_challenges DROP CONSTRAINT IF EXISTS evidence_key_registration_challenges_role_check;
ALTER TABLE evidence_key_registration_challenges ADD CONSTRAINT ck_evidence_key_challenge_role
    CHECK (role IN ('controller','processor'));
ALTER TABLE evidence_key_registration_challenges ALTER COLUMN instance_id SET NOT NULL;
ALTER TABLE evidence_key_registration_challenges ALTER COLUMN entity_id SET NOT NULL;
ALTER TABLE evidence_key_registration_challenges ALTER COLUMN action_sha256 SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_key_challenge_root_ceremony
    ON evidence_key_registration_challenges(root_ceremony_id) WHERE root_ceremony_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_evidence_key_challenge_action_sha256
    ON evidence_key_registration_challenges(action_sha256);

ALTER TABLE passkey_ceremonies
    ADD COLUMN IF NOT EXISTS action_json TEXT,
    ADD COLUMN IF NOT EXISTS action_sha256 VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_passkey_ceremonies_action_sha256
    ON passkey_ceremonies(action_sha256);

CREATE TABLE IF NOT EXISTS root_action_authorisations (
    id SERIAL PRIMARY KEY,
    authorisation_id VARCHAR(36) NOT NULL UNIQUE,
    instance_id VARCHAR(36) NOT NULL,
    root_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    credential_id_sha256 VARCHAR(64) NOT NULL,
    role VARCHAR(32) NOT NULL DEFAULT 'root_passkey' CHECK (role = 'root_passkey'),
    algorithm VARCHAR(32) NOT NULL DEFAULT 'WebAuthn',
    action_sha256 VARCHAR(64) NOT NULL UNIQUE,
    action_json TEXT NOT NULL,
    server_verified_at TIMESTAMPTZ NOT NULL,
    instance_record_sha256 VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_root_action_instance_id ON root_action_authorisations(instance_id);
CREATE INDEX IF NOT EXISTS ix_root_action_credential ON root_action_authorisations(credential_id_sha256);

COMMIT;
