-- Phase 3: explicit event grace deadlines and observable retention scheduling.

BEGIN;

ALTER TABLE events ADD COLUMN IF NOT EXISTS purge_grace_days INTEGER;
ALTER TABLE events ADD COLUMN IF NOT EXISTS purge_due_at TIMESTAMPTZ;
ALTER TABLE events ADD COLUMN IF NOT EXISTS purge_case_request_id VARCHAR(36);
ALTER TABLE events ADD COLUMN IF NOT EXISTS purge_started_at TIMESTAMPTZ;
ALTER TABLE events DROP CONSTRAINT IF EXISTS ck_event_purge_grace_days;
ALTER TABLE events ADD CONSTRAINT ck_event_purge_grace_days CHECK (
    purge_grace_days IS NULL OR purge_grace_days BETWEEN 1 AND 3650
);
CREATE INDEX IF NOT EXISTS ix_events_purge_due_at ON events(purge_due_at);
CREATE UNIQUE INDEX IF NOT EXISTS ix_events_purge_case_request_id
    ON events(purge_case_request_id) WHERE purge_case_request_id IS NOT NULL;

ALTER TABLE deletion_cases
    ADD COLUMN IF NOT EXISTS initiation_reason VARCHAR(32) NOT NULL DEFAULT 'authenticated_request';
ALTER TABLE deletion_cases ADD COLUMN IF NOT EXISTS event_purge_key VARCHAR(36);
UPDATE deletion_cases
SET initiation_reason = 'manual_root'
WHERE case_type = 'event_erasure';
WITH ranked AS (
    SELECT id, ROW_NUMBER() OVER (
        PARTITION BY event_evidence_id ORDER BY id DESC
    ) AS position
    FROM deletion_cases
    WHERE case_type = 'event_erasure'
)
UPDATE deletion_cases AS target
SET event_purge_key = target.event_evidence_id
FROM ranked
WHERE target.id = ranked.id AND ranked.position = 1;
ALTER TABLE deletion_cases DROP CONSTRAINT IF EXISTS ck_deletion_case_initiation_reason;
ALTER TABLE deletion_cases ADD CONSTRAINT ck_deletion_case_initiation_reason CHECK (
    initiation_reason IN (
        'authenticated_request','external_controller','manual_root','retention_schedule'
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_deletion_cases_event_purge_key
    ON deletion_cases(event_purge_key) WHERE event_purge_key IS NOT NULL;
UPDATE events AS event
SET purge_case_request_id = deletion.request_id,
    purge_started_at = deletion.submitted_at
FROM deletion_cases AS deletion
WHERE deletion.event_purge_key = event.evidence_id;

CREATE TABLE IF NOT EXISTS retention_scheduler_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cycle_count INTEGER NOT NULL DEFAULT 0,
    last_started_at TIMESTAMPTZ,
    last_completed_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_result VARCHAR(24),
    last_error_code VARCHAR(64),
    last_counts_json TEXT,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

COMMIT;
