BEGIN;

ALTER TABLE data_policy_acknowledgements
    ADD COLUMN IF NOT EXISTS policy_sha256 VARCHAR(64);

UPDATE data_policy_acknowledgements AS acknowledgement
SET policy_sha256 = publication.content_sha256
FROM governance_publications AS publication
WHERE acknowledgement.policy_version = publication.version
  AND acknowledgement.policy_sha256 IS NULL;

DELETE FROM data_policy_acknowledgements
WHERE policy_sha256 IS NULL;

ALTER TABLE data_policy_acknowledgements
    ALTER COLUMN policy_sha256 SET NOT NULL;

COMMIT;
