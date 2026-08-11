-- Retire the optional governance telephone field. Published historical
-- governance records remain immutable; the editable deployment profile is
-- email-only from Server v3.9.5 onward.
ALTER TABLE instance_governance_profile
    DROP COLUMN IF EXISTS privacy_contact_phone;
