BEGIN;

ALTER TABLE activation_links
    ADD COLUMN IF NOT EXISTS delivery_pending BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS activation_email_deliveries (
    id SERIAL PRIMARY KEY,
    activation_link_id INTEGER REFERENCES activation_links(id) ON DELETE SET NULL,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    requested_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    retry_of_id INTEGER REFERENCES activation_email_deliveries(id) ON DELETE SET NULL,
    recipient_email VARCHAR(320) NOT NULL,
    message_id VARCHAR(255),
    status VARCHAR(32) NOT NULL DEFAULT 'sending'
        CONSTRAINT ck_activation_email_delivery_status
        CHECK (status IN ('sending', 'accepted', 'failed', 'unknown', 'not_attempted')),
    error_code VARCHAR(64),
    error_message VARCHAR(255),
    includes_qr BOOLEAN NOT NULL DEFAULT TRUE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_activation_email_deliveries_user_id
    ON activation_email_deliveries(user_id);
CREATE INDEX IF NOT EXISTS ix_activation_email_deliveries_activation_link_id
    ON activation_email_deliveries(activation_link_id);
CREATE INDEX IF NOT EXISTS ix_activation_email_deliveries_status
    ON activation_email_deliveries(status);

UPDATE activation_email_deliveries
SET status = 'accepted'
WHERE status = 'sent';

UPDATE activation_links AS link
SET invalidated_at = CURRENT_TIMESTAMP,
    delivery_pending = FALSE
FROM activation_email_deliveries AS delivery
WHERE delivery.status = 'sending'
  AND delivery.activation_link_id = link.id
  AND link.used_at IS NULL;

UPDATE activation_email_deliveries
SET status = 'unknown',
    error_code = 'delivery_interrupted',
    error_message = 'Delivery was interrupted during upgrade. The activation link was invalidated; send a fresh email.',
    completed_at = CURRENT_TIMESTAMP
WHERE status = 'sending';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_activation_email_delivery_status'
    ) THEN
        ALTER TABLE activation_email_deliveries
            ADD CONSTRAINT ck_activation_email_delivery_status
            CHECK (status IN ('sending', 'accepted', 'failed', 'unknown', 'not_attempted'));
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_activation_email_delivery_sending_user
    ON activation_email_deliveries(user_id)
    WHERE status = 'sending';

UPDATE server_settings
SET value = '168'
WHERE key = 'activation_link_expiry_hours'
  AND value ~ '^[0-9]+$'
  AND value::integer > 168;

-- Move installations that still use the previous 48-hour default to the new
-- secure default. Explicitly customised values inside the supported range are
-- preserved.
UPDATE server_settings
SET value = '24'
WHERE key = 'activation_link_expiry_hours'
  AND value = '48';

COMMIT;
