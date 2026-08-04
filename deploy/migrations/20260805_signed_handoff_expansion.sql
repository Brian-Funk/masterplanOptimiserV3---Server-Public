-- Fresh test commissioning briefly runs the signed stable backend before the
-- exact campaign images are activated. Preserve its schema expectation during
-- that bounded handoff. Campaign code never reads or writes this obsolete
-- column, and no trust-declaration data or workflow is restored.
ALTER TABLE evidence_keys
    ADD COLUMN IF NOT EXISTS trust_declaration_sha256 VARCHAR(64);
