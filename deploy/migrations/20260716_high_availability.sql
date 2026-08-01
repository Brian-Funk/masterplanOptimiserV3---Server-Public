BEGIN;

CREATE TABLE IF NOT EXISTS ha_cluster_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cluster_id VARCHAR(128) NOT NULL,
    generation BIGINT NOT NULL CHECK (generation >= 1),
    active_node_id VARCHAR(128) NOT NULL,
    maintenance BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ha_node_heartbeats (
    node_id VARCHAR(128) PRIMARY KEY,
    role VARCHAR(16) NOT NULL CHECK (role IN ('active', 'standby')),
    generation BIGINT NOT NULL CHECK (generation >= 1),
    database_in_recovery BOOLEAN NOT NULL,
    database_writable BOOLEAN NOT NULL,
    replay_lsn VARCHAR(64),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_ha_node_heartbeats_observed_at
    ON ha_node_heartbeats (observed_at);

COMMIT;
