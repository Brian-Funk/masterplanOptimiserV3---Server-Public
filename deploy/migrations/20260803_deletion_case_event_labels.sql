BEGIN;

ALTER TABLE deletion_cases
    ADD COLUMN IF NOT EXISTS event_display_name VARCHAR(128);

-- Existing open cases can use their still-live event row. Cases whose event
-- was already purged remain unlabeled rather than inventing evidence.
UPDATE deletion_cases AS deletion_case
SET event_display_name = event.name
FROM events AS event
WHERE deletion_case.event_evidence_id = event.evidence_id
  AND deletion_case.event_display_name IS NULL
  AND deletion_case.state <> 'complete';

UPDATE deletion_cases
SET event_display_name = NULL
WHERE state = 'complete';

COMMIT;
