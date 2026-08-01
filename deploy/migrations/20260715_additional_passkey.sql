BEGIN;

ALTER TABLE activation_email_deliveries
    ADD COLUMN IF NOT EXISTS purpose VARCHAR(32);

UPDATE activation_email_deliveries AS delivery
SET purpose = link.purpose
FROM activation_links AS link
WHERE delivery.purpose IS NULL
  AND delivery.activation_link_id = link.id;

UPDATE activation_email_deliveries AS delivery
SET purpose = CASE
        WHEN account.is_activated THEN 'credential_reset'
        ELSE 'initial_setup'
    END
FROM users AS account
WHERE delivery.purpose IS NULL
  AND delivery.user_id = account.id;

UPDATE activation_email_deliveries
SET purpose = 'initial_setup'
WHERE purpose IS NULL;

ALTER TABLE activation_email_deliveries
    ALTER COLUMN purpose SET DEFAULT 'initial_setup',
    ALTER COLUMN purpose SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_activation_email_delivery_purpose'
    ) THEN
        ALTER TABLE activation_email_deliveries
            ADD CONSTRAINT ck_activation_email_delivery_purpose
            CHECK (purpose IN ('initial_setup', 'additional_passkey', 'credential_reset'));
    END IF;
END
$$;

COMMIT;
