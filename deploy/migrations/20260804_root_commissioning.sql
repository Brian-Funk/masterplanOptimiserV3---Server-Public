-- Clean-install campaign: obsolete trust declarations are deliberately not
-- migrated into the controller registration trust model.
ALTER TABLE evidence_keys
    DROP COLUMN IF EXISTS trust_declaration_sha256;

ALTER TABLE evidence_keys
    ADD COLUMN IF NOT EXISTS trust_establishment_sha256 VARCHAR(64);
