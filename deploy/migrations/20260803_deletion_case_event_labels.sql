BEGIN;

ALTER TABLE deletion_cases
    ADD COLUMN IF NOT EXISTS event_display_name VARCHAR(128);
ALTER TABLE deletion_cases
    ADD COLUMN IF NOT EXISTS subject_display_name VARCHAR(128);
ALTER TABLE deletion_cases
    ADD COLUMN IF NOT EXISTS desktop_absence_receipt_sha256 VARCHAR(64);
ALTER TABLE deletion_cases
    ADD COLUMN IF NOT EXISTS backup_not_applicable_sha256 VARCHAR(64);

-- Existing open cases can use their still-live event row. Cases whose event
-- was already purged remain unlabeled rather than inventing evidence.
UPDATE deletion_cases AS deletion_case
SET event_display_name = event.name
FROM events AS event
WHERE deletion_case.event_evidence_id = event.evidence_id
  AND deletion_case.event_display_name IS NULL
  AND deletion_case.state <> 'complete';

UPDATE deletion_cases AS deletion_case
SET subject_display_name = COALESCE(NULLIF(app_user.display_name, ''), app_user.username)
FROM users AS app_user
WHERE deletion_case.user_id = app_user.id
  AND deletion_case.subject_display_name IS NULL
  AND deletion_case.state <> 'complete';

UPDATE deletion_cases
SET event_display_name = NULL,
    subject_display_name = NULL
WHERE state = 'complete';

COMMIT;
