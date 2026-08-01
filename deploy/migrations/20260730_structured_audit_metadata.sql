-- Remove denormalised account names and add a random pseudonymous actor link.
-- New rows are constrained by the application audit vocabulary and bounded
-- JSON-object schema. Existing free-form detail remains subject to retention
-- and later deletion handling; it is not copied into the new actor reference.
ALTER TABLE audit_log
    ADD COLUMN IF NOT EXISTS actor_ref VARCHAR(36);

CREATE INDEX IF NOT EXISTS ix_audit_log_actor_ref
    ON audit_log (actor_ref);

UPDATE audit_log SET username = NULL WHERE username IS NOT NULL;
