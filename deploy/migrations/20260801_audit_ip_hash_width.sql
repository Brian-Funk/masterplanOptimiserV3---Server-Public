-- Versioned IP pseudonyms include a key identifier plus a bounded HMAC.
-- The original VARCHAR(32) column could not store the production v1 format,
-- which caused otherwise successful security actions to roll back at audit.
ALTER TABLE audit_log
    ALTER COLUMN ip_hash TYPE VARCHAR(80);
