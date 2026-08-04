-- Expand before contract: fresh test commissioning briefly runs the signed
-- stable backend before the exact campaign images are built. The old column
-- remains physically present for that handoff but is not read or written by
-- the campaign application; no declaration data or workflow is migrated.
ALTER TABLE evidence_keys
    ADD COLUMN IF NOT EXISTS trust_establishment_sha256 VARCHAR(64);
