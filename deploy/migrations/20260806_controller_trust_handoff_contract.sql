-- The unsigned commissioning lane now starts only the pinned campaign backend.
-- The expansion column retained for the former signed-to-unsigned application
-- handoff is therefore no longer needed. Deployment stops the old backend
-- before applying this contract migration.
ALTER TABLE evidence_keys
    DROP COLUMN IF EXISTS trust_declaration_sha256;
