BEGIN;

-- The earlier provider-specific streaming design stored database heartbeat
-- roles here. Symmetric ownership now lives in the external serialized lease;
-- keeping this unused active/standby table would create a second, misleading
-- source of truth.
DROP TABLE IF EXISTS ha_node_heartbeats;

COMMIT;
