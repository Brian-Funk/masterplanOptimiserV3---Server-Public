BEGIN;

CREATE SEQUENCE IF NOT EXISTS ha_protection_mutation_sequence AS BIGINT START WITH 1;

CREATE TABLE IF NOT EXISTS ha_protection_operations (
    id VARCHAR(36) PRIMARY KEY,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    operation_type VARCHAR(64) NOT NULL,
    resource_type VARCHAR(64) NOT NULL,
    resource_id VARCHAR(128),
    mutation_sequence BIGINT NOT NULL DEFAULT nextval('ha_protection_mutation_sequence') UNIQUE,
    state VARCHAR(16) NOT NULL DEFAULT 'pending',
    stage VARCHAR(24) NOT NULL DEFAULT 'queued',
    accepted_bundle_id VARCHAR(128),
    accepted_bundle_sha256 VARCHAR(64),
    accepted_generation BIGINT,
    error_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    accepted_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    CONSTRAINT ck_ha_protection_state CHECK (
        state IN ('pending','accepted','indeterminate','failed','cancelled')
    )
);

CREATE INDEX IF NOT EXISTS ix_ha_protection_operation_type
    ON ha_protection_operations(operation_type);
CREATE INDEX IF NOT EXISTS ix_ha_protection_resource
    ON ha_protection_operations(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS ix_ha_protection_state
    ON ha_protection_operations(state);

COMMIT;
