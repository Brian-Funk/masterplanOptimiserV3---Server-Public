-- First-class operator/controller/event tenancy foundation.
--
-- Existing installations are migrated to one deterministic compatibility
-- controller. Hosted mode remains disabled until the application preflight
-- proves that the migrated operator/controller facts have been reviewed and
-- every non-root account has one valid event membership.

BEGIN;

CREATE TABLE IF NOT EXISTS controllers (
    id SERIAL PRIMARY KEY,
    public_id VARCHAR(36) NOT NULL UNIQUE,
    trust_entity_id VARCHAR(52) NOT NULL UNIQUE,
    code VARCHAR(64) NOT NULL UNIQUE,
    display_name VARCHAR(200) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'active', 'suspended', 'retired')
    ),
    created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO controllers (
    id, public_id, trust_entity_id, code, display_name, status, created_by_id
)
SELECT
    1,
    gen_random_uuid()::text,
    COALESCE(
        (SELECT entity_id FROM evidence_keys
         WHERE role = 'controller' ORDER BY activated_at DESC NULLS LAST, id DESC LIMIT 1),
        'ctl-' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 16)
    ),
    'default',
    COALESCE(
        (SELECT controller_legal_name FROM instance_governance_profile WHERE id = 1),
        'Default controller'
    ),
    CASE WHEN EXISTS (SELECT 1 FROM instance_governance_profile WHERE id = 1)
        THEN 'active' ELSE 'draft' END,
    (SELECT id FROM users WHERE is_root_admin = TRUE ORDER BY id LIMIT 1)
WHERE NOT EXISTS (SELECT 1 FROM controllers WHERE id = 1);

SELECT setval(
    pg_get_serial_sequence('controllers', 'id'),
    GREATEST((SELECT COALESCE(MAX(id), 1) FROM controllers), 1),
    TRUE
);

CREATE INDEX IF NOT EXISTS ix_controllers_public_id ON controllers(public_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_controllers_trust_entity_id ON controllers(trust_entity_id);
CREATE INDEX IF NOT EXISTS ix_controllers_code ON controllers(code);
CREATE INDEX IF NOT EXISTS ix_controllers_status ON controllers(status);

CREATE TABLE IF NOT EXISTS instance_operator_profiles (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    instance_id VARCHAR(36) NOT NULL UNIQUE,
    operator_type VARCHAR(24) NOT NULL CHECK (
        operator_type IN ('organisation', 'individual')
    ),
    operator_legal_name VARCHAR(200) NOT NULL,
    operator_postal_address VARCHAR(500) NOT NULL,
    operator_country VARCHAR(2) NOT NULL,
    privacy_contact_email VARCHAR(320) NOT NULL,
    service_description TEXT NOT NULL,
    security_summary TEXT NOT NULL,
    subprocessors_json TEXT NOT NULL DEFAULT '[]',
    hosting_regions_json TEXT NOT NULL DEFAULT '[]',
    fixed_retention_days INTEGER NOT NULL CHECK (
        fixed_retention_days BETWEEN 1 AND 3650
    ),
    dpa_url VARCHAR(500),
    subprocessor_schedule_url VARCHAR(500),
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO instance_operator_profiles (
    id, instance_id, operator_type, operator_legal_name,
    operator_postal_address, operator_country, privacy_contact_email,
    service_description, security_summary, subprocessors_json,
    hosting_regions_json, fixed_retention_days
)
SELECT
    1,
    instance_id,
    controller_type,
    controller_legal_name,
    controller_postal_address,
    controller_country,
    privacy_contact_email,
    COALESCE(NULLIF(processor_summary, ''), 'Self-hosted MP-OPT technical operation'),
    'Migrated single-controller installation. Review the operator profile before enabling hosted mode.',
    COALESCE(structured_json::jsonb -> 'processors', '[]'::jsonb)::text,
    COALESCE(structured_json::jsonb -> 'hosting_countries', '[]'::jsonb)::text,
    COALESCE(
        NULLIF(structured_json::jsonb #>> '{retention,event_grace_days}', '')::integer,
        90
    )
FROM instance_governance_profile
WHERE id = 1
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS operator_policy_publications (
    id SERIAL PRIMARY KEY,
    version INTEGER NOT NULL UNIQUE,
    content_json TEXT NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL UNIQUE,
    source_json TEXT NOT NULL DEFAULT '{}',
    source_sha256 VARCHAR(64) NOT NULL,
    published_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    supersedes_version INTEGER
);

-- Preserve the exact latest legacy publication as a migration reference. It is
-- explicitly marked as combined and must be replaced before hosted mode can be
-- enabled; immutable historical bytes and digests are not rewritten.
INSERT INTO operator_policy_publications (
    version, content_json, content_sha256, source_json, source_sha256,
    published_by_id, published_at, supersedes_version
)
SELECT
    1,
    jsonb_set(
        content_json::jsonb,
        '{migration}',
        '{"legacy_combined_operator_controller":true}'::jsonb,
        TRUE
    )::text,
    encode(digest(
        jsonb_set(
            content_json::jsonb,
            '{migration}',
            '{"legacy_combined_operator_controller":true}'::jsonb,
            TRUE
        )::text,
        'sha256'
    ), 'hex'),
    source_json,
    source_sha256,
    published_by_id,
    published_at,
    NULL
FROM governance_publications
ORDER BY version DESC
LIMIT 1
ON CONFLICT (version) DO NOTHING;

CREATE TABLE IF NOT EXISTS controller_governance_profiles (
    controller_id INTEGER PRIMARY KEY REFERENCES controllers(id) ON DELETE CASCADE,
    controller_type VARCHAR(24) NOT NULL CHECK (
        controller_type IN ('organisation', 'individual')
    ),
    legal_name VARCHAR(200) NOT NULL,
    postal_address VARCHAR(500) NOT NULL,
    country VARCHAR(2) NOT NULL,
    privacy_contact_email VARCHAR(320) NOT NULL,
    dpo_contact VARCHAR(320),
    supervisory_authority_name VARCHAR(200) NOT NULL,
    supervisory_authority_url VARCHAR(500) NOT NULL,
    default_locale VARCHAR(16) NOT NULL DEFAULT 'en',
    processor_summary TEXT NOT NULL,
    rights_summary TEXT NOT NULL,
    terms_summary TEXT NOT NULL,
    structured_json TEXT NOT NULL DEFAULT '{}',
    accepted_operator_policy_version INTEGER,
    accepted_operator_policy_sha256 VARCHAR(64),
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO controller_governance_profiles (
    controller_id, controller_type, legal_name, postal_address, country,
    privacy_contact_email, dpo_contact, supervisory_authority_name,
    supervisory_authority_url, default_locale, processor_summary,
    rights_summary, terms_summary, structured_json,
    accepted_operator_policy_version, accepted_operator_policy_sha256
)
SELECT
    1, controller_type, controller_legal_name, controller_postal_address,
    controller_country, privacy_contact_email, dpo_contact,
    supervisory_authority_name, supervisory_authority_url, default_locale,
    processor_summary, rights_summary, terms_summary, structured_json,
    (SELECT version FROM operator_policy_publications ORDER BY version DESC LIMIT 1),
    (SELECT content_sha256 FROM operator_policy_publications ORDER BY version DESC LIMIT 1)
FROM instance_governance_profile
WHERE id = 1
ON CONFLICT (controller_id) DO NOTHING;

ALTER TABLE events ADD COLUMN IF NOT EXISTS controller_id INTEGER;
UPDATE events SET controller_id = 1 WHERE controller_id IS NULL;
ALTER TABLE events ALTER COLUMN controller_id SET NOT NULL;
ALTER TABLE events DROP CONSTRAINT IF EXISTS fk_events_controller;
ALTER TABLE events ADD CONSTRAINT fk_events_controller
    FOREIGN KEY (controller_id) REFERENCES controllers(id) ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS ix_events_controller_id ON events(controller_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_event_id_controller ON events(id, controller_id);

CREATE TABLE IF NOT EXISTS controller_governance_publications (
    id SERIAL PRIMARY KEY,
    controller_id INTEGER NOT NULL REFERENCES controllers(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL,
    source_json TEXT NOT NULL DEFAULT '{}',
    source_sha256 VARCHAR(64) NOT NULL,
    controller_key_id INTEGER REFERENCES evidence_keys(id) ON DELETE SET NULL,
    technical_publisher_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    operator_policy_version INTEGER NOT NULL,
    operator_policy_sha256 VARCHAR(64) NOT NULL,
    external_authorisation_ref VARCHAR(200),
    evidence_record_sha256 VARCHAR(64),
    published_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    supersedes_version INTEGER,
    legacy_publication_id INTEGER UNIQUE REFERENCES governance_publications(id) ON DELETE SET NULL,
    CONSTRAINT uq_controller_governance_version UNIQUE (controller_id, version),
    CONSTRAINT uq_controller_governance_content UNIQUE (controller_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS ix_controller_governance_controller
    ON controller_governance_publications(controller_id);

INSERT INTO controller_governance_publications (
    controller_id, version, content_json, content_sha256, source_json,
    source_sha256, controller_key_id, technical_publisher_id,
    operator_policy_version, operator_policy_sha256, published_at,
    supersedes_version, legacy_publication_id
)
SELECT
    1,
    publication.version,
    publication.content_json,
    publication.content_sha256,
    publication.source_json,
    publication.source_sha256,
    (
        SELECT key.id FROM evidence_keys AS key
        WHERE key.role = 'controller'
          AND key.activated_at IS NOT NULL
          AND key.revoked_at IS NULL
        ORDER BY key.id
        LIMIT 1
    ),
    publication.published_by_id,
    operator.version,
    operator.content_sha256,
    publication.published_at,
    publication.supersedes_version,
    publication.id
FROM governance_publications AS publication
CROSS JOIN LATERAL (
    SELECT version, content_sha256
    FROM operator_policy_publications
    ORDER BY version DESC
    LIMIT 1
) AS operator
ON CONFLICT (controller_id, version) DO NOTHING;

CREATE TABLE IF NOT EXISTS event_governance_configurations (
    event_id INTEGER PRIMARY KEY,
    controller_id INTEGER NOT NULL,
    event_notice TEXT,
    enabled_optional_features_json TEXT NOT NULL DEFAULT '[]',
    contact_routing_json TEXT NOT NULL DEFAULT '{}',
    operator_policy_version INTEGER NOT NULL,
    controller_policy_version INTEGER NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    content_sha256 VARCHAR(64) NOT NULL,
    updated_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_event_governance_event_controller
        FOREIGN KEY (event_id, controller_id)
        REFERENCES events(id, controller_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_event_governance_controller
    ON event_governance_configurations(controller_id);
ALTER TABLE event_governance_configurations
    ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE event_governance_configurations
    ADD COLUMN IF NOT EXISTS content_sha256 VARCHAR(64);

INSERT INTO event_governance_configurations (
    event_id, controller_id, event_notice,
    enabled_optional_features_json, contact_routing_json,
    operator_policy_version, controller_policy_version,
    revision, content_sha256,
    updated_by_id, updated_at
)
SELECT
    event.id,
    event.controller_id,
    CASE WHEN override.controller_override_enabled THEN
        jsonb_build_object(
            'legacy_controller_override_requires_review', TRUE,
            'controller_identity', override.controller_identity_override,
            'privacy_contact', override.privacy_contact_override,
            'retention_override_days', override.retention_override_days
        )::text
    ELSE NULL END,
    COALESCE(override.enabled_optional_features_json, '[]'),
    '{}',
    operator.version,
    COALESCE(override.policy_version, controller.version),
    1,
    encode(digest(jsonb_build_object(
        'event_notice', CASE WHEN override.controller_override_enabled THEN
            jsonb_build_object(
                'legacy_controller_override_requires_review', TRUE,
                'controller_identity', override.controller_identity_override,
                'privacy_contact', override.privacy_contact_override,
                'retention_override_days', override.retention_override_days
            )::text ELSE NULL END,
        'enabled_optional_features', COALESCE(
            override.enabled_optional_features_json, '[]'
        )::jsonb,
        'contact_routing', '{}'::jsonb,
        'operator_policy_version', operator.version,
        'controller_policy_version', COALESCE(
            override.policy_version, controller.version
        )
    )::text, 'sha256'), 'hex'),
    override.updated_by_id,
    COALESCE(override.updated_at, CURRENT_TIMESTAMP)
FROM events AS event
CROSS JOIN LATERAL (
    SELECT version FROM operator_policy_publications ORDER BY version DESC LIMIT 1
) AS operator
CROSS JOIN LATERAL (
    SELECT version FROM controller_governance_publications
    WHERE controller_id = event.controller_id ORDER BY version DESC LIMIT 1
) AS controller
LEFT JOIN event_governance_overrides AS override ON override.event_id = event.id
ON CONFLICT (event_id) DO NOTHING;

UPDATE event_governance_configurations AS config
SET content_sha256 = encode(digest(jsonb_build_object(
    'event_notice', config.event_notice,
    'enabled_optional_features', config.enabled_optional_features_json::jsonb,
    'contact_routing', config.contact_routing_json::jsonb,
    'operator_policy_version', config.operator_policy_version,
    'controller_policy_version', config.controller_policy_version
)::text, 'sha256'), 'hex')
WHERE content_sha256 IS NULL;
ALTER TABLE event_governance_configurations
    ALTER COLUMN content_sha256 SET NOT NULL;
ALTER TABLE event_governance_configurations
    DROP CONSTRAINT IF EXISTS ck_event_governance_revision;
ALTER TABLE event_governance_configurations
    ADD CONSTRAINT ck_event_governance_revision CHECK (revision >= 1);

CREATE TABLE IF NOT EXISTS event_memberships (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    controller_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    is_event_admin BOOLEAN NOT NULL DEFAULT FALSE,
    is_issuer BOOLEAN NOT NULL DEFAULT FALSE,
    can_edit BOOLEAN NOT NULL DEFAULT FALSE,
    is_privacy_delegate BOOLEAN NOT NULL DEFAULT FALSE,
    linked_person_id INTEGER,
    status VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'suspended', 'revoked')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_event_membership_event_controller
        FOREIGN KEY (event_id, controller_id)
        REFERENCES events(id, controller_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_event_memberships_controller
    ON event_memberships(controller_id);
CREATE INDEX IF NOT EXISTS ix_event_memberships_event
    ON event_memberships(event_id);
CREATE INDEX IF NOT EXISTS ix_event_memberships_user
    ON event_memberships(user_id);
CREATE INDEX IF NOT EXISTS ix_event_memberships_status
    ON event_memberships(status);

CREATE OR REPLACE FUNCTION mp_opt_assert_user_membership_projection()
RETURNS trigger LANGUAGE plpgsql AS $membership_projection$
DECLARE
    target_user_id integer;
    account users%ROWTYPE;
    membership event_memberships%ROWTYPE;
    membership_found boolean;
    hosted boolean;
BEGIN
    target_user_id := CASE
        WHEN TG_TABLE_NAME = 'users' THEN COALESCE(NEW.id, OLD.id)
        ELSE COALESCE(NEW.user_id, OLD.user_id)
    END;
    SELECT * INTO account FROM users WHERE id = target_user_id;
    IF NOT FOUND THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO membership FROM event_memberships WHERE user_id = target_user_id;
    membership_found := FOUND;
    hosted := COALESCE(
        (SELECT value = 'hosted-multi-controller' FROM server_settings
         WHERE key = 'tenancy_mode'),
        FALSE
    );
    IF account.is_root_admin THEN
        IF membership_found THEN
            RAISE EXCEPTION 'root accounts cannot have event memberships';
        END IF;
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF NOT membership_found THEN
        IF hosted THEN
            RAISE EXCEPTION 'hosted non-root accounts require an event membership';
        END IF;
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF account.event_id IS DISTINCT FROM membership.event_id
       OR account.is_admin IS DISTINCT FROM membership.is_event_admin
       OR account.is_issuer IS DISTINCT FROM membership.is_issuer
       OR account.can_edit IS DISTINCT FROM membership.can_edit
       OR account.linked_person_id IS DISTINCT FROM membership.linked_person_id
       OR NOT EXISTS (
           SELECT 1 FROM events event
           WHERE event.id = membership.event_id
             AND event.controller_id = membership.controller_id
       ) THEN
        RAISE EXCEPTION 'account projection does not match its event membership';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$membership_projection$;

DROP TRIGGER IF EXISTS mp_opt_membership_projection ON event_memberships;
CREATE CONSTRAINT TRIGGER mp_opt_membership_projection
AFTER INSERT OR UPDATE OR DELETE ON event_memberships
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION mp_opt_assert_user_membership_projection();

DROP TRIGGER IF EXISTS mp_opt_user_membership_projection ON users;
CREATE CONSTRAINT TRIGGER mp_opt_user_membership_projection
AFTER INSERT OR UPDATE ON users
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION mp_opt_assert_user_membership_projection();

CREATE TABLE IF NOT EXISTS controller_evidence_chain_states (
    controller_id INTEGER PRIMARY KEY REFERENCES controllers(id) ON DELETE RESTRICT,
    instance_id VARCHAR(36) NOT NULL,
    controller_public_id VARCHAR(36) NOT NULL UNIQUE,
    controller_trust_entity_id VARCHAR(52) NOT NULL UNIQUE,
    chain_id VARCHAR(36) NOT NULL UNIQUE,
    evidence_mode VARCHAR(16) NOT NULL DEFAULT 'required',
    last_sequence INTEGER NOT NULL DEFAULT 0,
    head_sha256 VARCHAR(64),
    legacy_chain_head_sha256 VARCHAR(64),
    initialised_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verified_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_controller_evidence_instance
    ON controller_evidence_chain_states(instance_id);

ALTER TABLE evidence_operations
    ADD COLUMN IF NOT EXISTS chain_scope VARCHAR(16) NOT NULL DEFAULT 'operator',
    ADD COLUMN IF NOT EXISTS controller_id INTEGER REFERENCES controllers(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS event_id INTEGER REFERENCES events(id) ON DELETE SET NULL;
ALTER TABLE evidence_operations DROP CONSTRAINT IF EXISTS uq_evidence_operation;
ALTER TABLE evidence_operations DROP CONSTRAINT IF EXISTS ck_evidence_operation_scope;
ALTER TABLE evidence_operations ADD CONSTRAINT uq_evidence_operation
    UNIQUE (workflow_type, workflow_id, operation_type);
ALTER TABLE evidence_operations ADD CONSTRAINT ck_evidence_operation_scope CHECK (
    (chain_scope = 'operator' AND controller_id IS NULL AND event_id IS NULL)
    OR (chain_scope = 'controller' AND controller_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_evidence_operations_scope
    ON evidence_operations(chain_scope);
CREATE INDEX IF NOT EXISTS ix_evidence_operations_controller
    ON evidence_operations(controller_id);
CREATE INDEX IF NOT EXISTS ix_evidence_operations_event
    ON evidence_operations(event_id);

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_username_key;
DROP INDEX IF EXISTS uq_users_username;
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_event_username
    ON users(event_id, username) WHERE event_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_root_username
    ON users(username) WHERE is_root_admin = TRUE;

INSERT INTO event_memberships (
    user_id, controller_id, event_id, is_event_admin, is_issuer,
    can_edit, linked_person_id, status
)
SELECT
    user_account.id,
    event.controller_id,
    event.id,
    user_account.is_admin,
    user_account.is_issuer,
    user_account.can_edit,
    user_account.linked_person_id,
    CASE WHEN user_account.is_active THEN 'active' ELSE 'suspended' END
FROM users AS user_account
JOIN events AS event ON event.id = user_account.event_id
WHERE user_account.is_root_admin = FALSE
ON CONFLICT (user_id) DO NOTHING;

ALTER TABLE audit_log
    ADD COLUMN IF NOT EXISTS controller_id INTEGER REFERENCES controllers(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS event_id INTEGER REFERENCES events(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_audit_log_controller_id ON audit_log(controller_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_event_id ON audit_log(event_id);

ALTER TABLE data_policy_acknowledgements
    ADD COLUMN IF NOT EXISTS controller_id INTEGER REFERENCES controllers(id) ON DELETE RESTRICT;
UPDATE data_policy_acknowledgements AS acknowledgement
SET controller_id = event.controller_id
FROM events AS event
WHERE acknowledgement.event_id = event.id
  AND acknowledgement.controller_id IS NULL;
CREATE INDEX IF NOT EXISTS ix_data_policy_ack_controller
    ON data_policy_acknowledgements(controller_id);
ALTER TABLE data_policy_acknowledgements ADD CONSTRAINT ck_data_policy_ack_controller_scope
    CHECK (event_id IS NULL OR controller_id IS NOT NULL);
ALTER TABLE data_policy_acknowledgements
    ADD CONSTRAINT fk_data_policy_ack_event_controller
    FOREIGN KEY (event_id, controller_id)
    REFERENCES events(id, controller_id) ON DELETE CASCADE;

ALTER TABLE account_processing_consents
    ADD COLUMN IF NOT EXISTS controller_id INTEGER REFERENCES controllers(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS confirmation_type VARCHAR(32) NOT NULL DEFAULT 'disclosure_acknowledgement',
    ADD COLUMN IF NOT EXISTS legal_basis_code VARCHAR(64),
    ADD COLUMN IF NOT EXISTS operator_policy_version INTEGER,
    ADD COLUMN IF NOT EXISTS operator_policy_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS controller_policy_version INTEGER,
    ADD COLUMN IF NOT EXISTS controller_policy_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS event_notice_revision INTEGER,
    ADD COLUMN IF NOT EXISTS event_notice_sha256 VARCHAR(64);
UPDATE account_processing_consents AS consent
SET controller_id = event.controller_id
FROM events AS event
WHERE consent.event_id = event.id
  AND consent.controller_id IS NULL;
CREATE INDEX IF NOT EXISTS ix_account_processing_consents_controller
    ON account_processing_consents(controller_id);
ALTER TABLE account_processing_consents ALTER COLUMN controller_id SET NOT NULL;

ALTER TABLE deletion_cases
    ADD COLUMN IF NOT EXISTS controller_id INTEGER REFERENCES controllers(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS event_id INTEGER;
UPDATE deletion_cases AS deletion_case
SET controller_id = event.controller_id,
    event_id = event.id
FROM events AS event
WHERE deletion_case.event_evidence_id = event.evidence_id
  AND (deletion_case.controller_id IS NULL OR deletion_case.event_id IS NULL);
UPDATE deletion_cases
SET controller_id = 1
WHERE controller_id IS NULL;
ALTER TABLE deletion_cases ALTER COLUMN controller_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS ix_deletion_cases_controller_id
    ON deletion_cases(controller_id);
CREATE INDEX IF NOT EXISTS ix_deletion_cases_event_id
    ON deletion_cases(event_id);

ALTER TABLE evidence_keys
    ADD COLUMN IF NOT EXISTS controller_id INTEGER REFERENCES controllers(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS event_id INTEGER REFERENCES events(id) ON DELETE SET NULL;
UPDATE evidence_keys SET controller_id = 1
WHERE role = 'controller' AND controller_id IS NULL;
CREATE INDEX IF NOT EXISTS ix_evidence_keys_controller_id
    ON evidence_keys(controller_id);

ALTER TABLE processor_identities
    ADD COLUMN IF NOT EXISTS controller_id INTEGER REFERENCES controllers(id) ON DELETE RESTRICT;
UPDATE processor_identities AS identity
SET controller_id = event.controller_id
FROM events AS event
WHERE identity.event_id = event.id
  AND identity.controller_id IS NULL;
CREATE INDEX IF NOT EXISTS ix_processor_identities_controller_id
    ON processor_identities(controller_id);
ALTER TABLE processor_identities ALTER COLUMN controller_id SET NOT NULL;

UPDATE evidence_keys AS key
SET controller_id = identity.controller_id
FROM processor_identities AS identity
WHERE key.role = 'processor'
  AND key.entity_id = identity.entity_id
  AND key.controller_id IS NULL;
UPDATE evidence_keys AS key
SET event_id = identity.event_id
FROM processor_identities AS identity
WHERE key.role = 'processor'
  AND key.entity_id = identity.entity_id
  AND key.event_id IS NULL;
ALTER TABLE evidence_keys ADD CONSTRAINT ck_evidence_key_controller_scope CHECK (
    (role = 'instance' AND controller_id IS NULL)
    OR (role IN ('controller', 'processor') AND controller_id IS NOT NULL)
);
ALTER TABLE evidence_keys ADD CONSTRAINT ck_evidence_key_event_scope CHECK (
    (role = 'processor' AND (event_id IS NOT NULL OR revoked_at IS NOT NULL))
    OR (role IN ('instance', 'controller') AND event_id IS NULL)
);
CREATE INDEX IF NOT EXISTS ix_evidence_keys_event_id ON evidence_keys(event_id);

ALTER TABLE processor_policy_acknowledgements
    ADD COLUMN IF NOT EXISTS controller_id INTEGER REFERENCES controllers(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS event_id INTEGER REFERENCES events(id) ON DELETE SET NULL;
UPDATE processor_policy_acknowledgements AS acknowledgement
SET controller_id = event.controller_id,
    event_id = event.id
FROM events AS event
WHERE acknowledgement.event_evidence_id = event.evidence_id
  AND (acknowledgement.controller_id IS NULL OR acknowledgement.event_id IS NULL);
CREATE INDEX IF NOT EXISTS ix_processor_policy_ack_controller_id
    ON processor_policy_acknowledgements(controller_id);
ALTER TABLE processor_policy_acknowledgements ALTER COLUMN controller_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS ix_processor_policy_ack_event_id
    ON processor_policy_acknowledgements(event_id);

ALTER TABLE evidence_key_registration_challenges
    ADD COLUMN IF NOT EXISTS controller_id INTEGER REFERENCES controllers(id) ON DELETE RESTRICT;
UPDATE evidence_key_registration_challenges AS challenge
SET controller_id = event.controller_id
FROM events AS event
WHERE challenge.event_id = event.id
  AND challenge.controller_id IS NULL;
UPDATE evidence_key_registration_challenges
SET controller_id = 1
WHERE role = 'controller' AND controller_id IS NULL;
CREATE INDEX IF NOT EXISTS ix_evidence_key_challenges_controller_id
    ON evidence_key_registration_challenges(controller_id);
ALTER TABLE evidence_key_registration_challenges ALTER COLUMN controller_id SET NOT NULL;

-- Retained evidence and audit rows intentionally keep their controller after
-- a live event is deleted, so a composite FK with ON DELETE SET NULL would be
-- incorrect. Enforce the same-controller invariant on creation and on every
-- scope-changing update, while allowing later receipt/status updates after
-- the referenced live event has been purged.
CREATE OR REPLACE FUNCTION mp_opt_assert_event_controller_match()
RETURNS trigger
LANGUAGE plpgsql
AS $scope$
DECLARE
    actual_controller_id integer;
BEGIN
    IF NEW.event_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT controller_id INTO actual_controller_id
    FROM events WHERE id = NEW.event_id;
    IF FOUND THEN
        IF NEW.controller_id IS DISTINCT FROM actual_controller_id THEN
            RAISE EXCEPTION 'event/controller tenant scope mismatch';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE'
       AND OLD.event_id IS NOT DISTINCT FROM NEW.event_id
       AND OLD.controller_id IS NOT DISTINCT FROM NEW.controller_id THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'tenant-scoped event does not exist';
END
$scope$;

-- Tenant ownership and public trust identities are immutable.  Reassigning an
-- event would silently move all of its historical rows, evidence and privacy
-- obligations into another legal controller.  A replacement event plus an
-- explicit export/import ceremony is required instead.
CREATE OR REPLACE FUNCTION mp_opt_reject_event_controller_reassignment()
RETURNS trigger LANGUAGE plpgsql AS $immutable_event_controller$
BEGIN
    IF OLD.controller_id IS DISTINCT FROM NEW.controller_id THEN
        RAISE EXCEPTION 'event controller ownership is immutable';
    END IF;
    RETURN NEW;
END
$immutable_event_controller$;

DROP TRIGGER IF EXISTS mp_opt_event_controller_immutable ON events;
CREATE TRIGGER mp_opt_event_controller_immutable
BEFORE UPDATE OF controller_id ON events
FOR EACH ROW EXECUTE FUNCTION mp_opt_reject_event_controller_reassignment();

CREATE OR REPLACE FUNCTION mp_opt_reject_controller_identity_change()
RETURNS trigger LANGUAGE plpgsql AS $immutable_controller_identity$
BEGIN
    IF OLD.public_id IS DISTINCT FROM NEW.public_id
       OR OLD.trust_entity_id IS DISTINCT FROM NEW.trust_entity_id
       OR OLD.code IS DISTINCT FROM NEW.code THEN
        RAISE EXCEPTION 'controller public trust identity is immutable';
    END IF;
    RETURN NEW;
END
$immutable_controller_identity$;

DROP TRIGGER IF EXISTS mp_opt_controller_identity_immutable ON controllers;
CREATE TRIGGER mp_opt_controller_identity_immutable
BEFORE UPDATE OF public_id, trust_entity_id, code ON controllers
FOR EACH ROW EXECUTE FUNCTION mp_opt_reject_controller_identity_change();

DO $scope_triggers$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'account_processing_consents', 'evidence_keys',
        'processor_identities', 'processor_policy_acknowledgements',
        'evidence_key_registration_challenges', 'deletion_cases',
        'evidence_operations', 'audit_log'
    ] LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS mp_opt_event_controller_match ON %I',
            table_name
        );
        EXECUTE format(
            'CREATE TRIGGER mp_opt_event_controller_match '
            'BEFORE INSERT OR UPDATE OF event_id, controller_id ON %I '
            'FOR EACH ROW EXECUTE FUNCTION mp_opt_assert_event_controller_match()',
            table_name
        );
    END LOOP;
END
$scope_triggers$;

-- Defense in depth for the runtime database role. FORCE applies the policy
-- even when that role owns the tables. Infrastructure root/database-owner
-- access remains explicitly outside the tenant confidentiality boundary.
CREATE OR REPLACE FUNCTION mp_opt_rls_root() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(current_setting('mp_opt.is_root', true), 'false') = 'true'
$$;
CREATE OR REPLACE FUNCTION mp_opt_rls_event_id() RETURNS integer
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('mp_opt.event_id', true), '')::integer
$$;
CREATE OR REPLACE FUNCTION mp_opt_rls_controller_id() RETURNS integer
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('mp_opt.controller_id', true), '')::integer
$$;
CREATE OR REPLACE FUNCTION mp_opt_rls_scope() RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(current_setting('mp_opt.scope', true), 'deny')
$$;
CREATE OR REPLACE FUNCTION mp_opt_rls_user_id() RETURNS integer
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('mp_opt.user_id', true), '')::integer
$$;

DO $rls$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'event_governance_configurations', 'event_memberships',
        'event_governance_overrides', 'published_persons',
        'published_person_unavailability', 'published_tasks',
        'publish_snapshots', 'published_general_schedule_categories',
        'published_general_schedule_items', 'general_schedule_publish_state',
        'announcements', 'schedule_changes', 'push_subscriptions',
        'public_schedule_links'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS mp_opt_event_scope ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY mp_opt_event_scope ON %I USING '
            '(mp_opt_rls_root() OR event_id = mp_opt_rls_event_id()) WITH CHECK '
            '(mp_opt_rls_root() OR event_id = mp_opt_rls_event_id())', table_name
        );
    END LOOP;
END
$rls$;

ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_event_scope ON events;
CREATE POLICY mp_opt_event_scope ON events
    USING (
        mp_opt_rls_root() OR id = mp_opt_rls_event_id()
        OR mp_opt_rls_scope() IN ('publisher_lookup', 'public_event_lookup')
    )
    WITH CHECK (mp_opt_rls_root() OR id = mp_opt_rls_event_id());

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_event_scope ON users;
CREATE POLICY mp_opt_event_scope ON users
    USING (
        mp_opt_rls_root()
        OR id = mp_opt_rls_user_id()
        OR event_id = mp_opt_rls_event_id()
    )
    WITH CHECK (
        mp_opt_rls_root()
        OR id = mp_opt_rls_user_id()
        OR event_id = mp_opt_rls_event_id()
    );

-- Authentication data is user/event scoped once identity is known.  The
-- narrowly named service scope exists only for resolving opaque sessions,
-- exchange codes, activation tokens and discoverable passkey credentials
-- before their user and event can be known.
DO $auth_rls$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'webauthn_credentials', 'exchange_codes', 'auth_sessions',
        'activation_links', 'activation_email_deliveries'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS mp_opt_auth_scope ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY mp_opt_auth_scope ON %I USING ('
            'mp_opt_rls_root() OR mp_opt_rls_scope() = ''authentication_service'' OR '
            'EXISTS (SELECT 1 FROM users u WHERE u.id = user_id AND '
            '(u.id = mp_opt_rls_user_id() OR u.event_id = mp_opt_rls_event_id()))'
            ') WITH CHECK ('
            'mp_opt_rls_root() OR mp_opt_rls_scope() = ''authentication_service'' OR '
            'EXISTS (SELECT 1 FROM users u WHERE u.id = user_id AND '
            '(u.id = mp_opt_rls_user_id() OR u.event_id = mp_opt_rls_event_id()))'
            ')', table_name
        );
    END LOOP;
END
$auth_rls$;

DO $ceremony_rls$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['passkey_challenges', 'passkey_ceremonies'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS mp_opt_auth_scope ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY mp_opt_auth_scope ON %I USING ('
            'mp_opt_rls_root() OR mp_opt_rls_scope() = ''authentication_service'' OR '
            'user_id = mp_opt_rls_user_id()'
            ') WITH CHECK ('
            'mp_opt_rls_root() OR mp_opt_rls_scope() = ''authentication_service'' OR '
            'user_id = mp_opt_rls_user_id()'
            ')', table_name
        );
    END LOOP;
END
$ceremony_rls$;

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_audit_select ON audit_log;
CREATE POLICY mp_opt_audit_select ON audit_log FOR SELECT
    USING (mp_opt_rls_root() OR event_id = mp_opt_rls_event_id());
DROP POLICY IF EXISTS mp_opt_audit_insert ON audit_log;
CREATE POLICY mp_opt_audit_insert ON audit_log FOR INSERT
    WITH CHECK (
        mp_opt_rls_root()
        OR event_id = mp_opt_rls_event_id()
        OR (user_id IS NULL AND event_id IS NULL AND controller_id IS NULL)
    );
DROP POLICY IF EXISTS mp_opt_audit_update ON audit_log;
CREATE POLICY mp_opt_audit_update ON audit_log FOR UPDATE
    USING (mp_opt_rls_root() OR event_id = mp_opt_rls_event_id())
    WITH CHECK (mp_opt_rls_root() OR event_id = mp_opt_rls_event_id());

DROP POLICY IF EXISTS mp_opt_public_link_lookup ON public_schedule_links;
CREATE POLICY mp_opt_public_link_lookup ON public_schedule_links
    FOR SELECT USING (mp_opt_rls_scope() = 'public_link_lookup');

ALTER TABLE task_edits ENABLE ROW LEVEL SECURITY;
ALTER TABLE task_edits FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_event_scope ON task_edits;
CREATE POLICY mp_opt_event_scope ON task_edits
    USING (mp_opt_rls_root() OR EXISTS (
        SELECT 1 FROM published_tasks task
        WHERE task.id = task_edits.task_id
          AND task.event_id = mp_opt_rls_event_id()
    ))
    WITH CHECK (mp_opt_rls_root() OR EXISTS (
        SELECT 1 FROM published_tasks task
        WHERE task.id = task_edits.task_id
          AND task.event_id = mp_opt_rls_event_id()
    ));

ALTER TABLE public_schedule_link_views ENABLE ROW LEVEL SECURITY;
ALTER TABLE public_schedule_link_views FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_event_scope ON public_schedule_link_views;
CREATE POLICY mp_opt_event_scope ON public_schedule_link_views
    USING (mp_opt_rls_root() OR EXISTS (
        SELECT 1 FROM public_schedule_links link
        WHERE link.id = public_schedule_link_views.link_id
          AND link.event_id = mp_opt_rls_event_id()
    ))
    WITH CHECK (mp_opt_rls_root() OR EXISTS (
        SELECT 1 FROM public_schedule_links link
        WHERE link.id = public_schedule_link_views.link_id
          AND link.event_id = mp_opt_rls_event_id()
    ));

DO $controller_rls$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'controller_governance_profiles', 'controller_governance_publications'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS mp_opt_controller_scope ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY mp_opt_controller_scope ON %I USING '
            '(mp_opt_rls_root() OR controller_id = mp_opt_rls_controller_id()) WITH CHECK '
            '(mp_opt_rls_root() OR controller_id = mp_opt_rls_controller_id())', table_name
        );
    END LOOP;
END
$controller_rls$;

DO $processor_rls$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'processor_identities', 'processor_policy_acknowledgements'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS mp_opt_event_scope ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY mp_opt_event_scope ON %I USING '
            '(mp_opt_rls_root() OR (event_id = mp_opt_rls_event_id() AND '
            'controller_id = mp_opt_rls_controller_id())) WITH CHECK '
            '(mp_opt_rls_root() OR (event_id = mp_opt_rls_event_id() AND '
            'controller_id = mp_opt_rls_controller_id()))', table_name
        );
    END LOOP;
END
$processor_rls$;

ALTER TABLE evidence_key_registration_challenges ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_key_registration_challenges FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_event_scope ON evidence_key_registration_challenges;
CREATE POLICY mp_opt_event_scope ON evidence_key_registration_challenges
    USING (
        mp_opt_rls_root()
        OR (
            role = 'processor'
            AND event_id = mp_opt_rls_event_id()
            AND controller_id = mp_opt_rls_controller_id()
        )
    )
    WITH CHECK (
        mp_opt_rls_root()
        OR (
            role = 'processor'
            AND event_id = mp_opt_rls_event_id()
            AND controller_id = mp_opt_rls_controller_id()
        )
    );

ALTER TABLE controllers ENABLE ROW LEVEL SECURITY;
ALTER TABLE controllers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_controller_scope ON controllers;
CREATE POLICY mp_opt_controller_scope ON controllers
    USING (
        mp_opt_rls_root() OR id = mp_opt_rls_controller_id()
        OR mp_opt_rls_scope() = 'public_controller_lookup'
    )
    WITH CHECK (mp_opt_rls_root());

ALTER TABLE evidence_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_keys FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_controller_scope ON evidence_keys;
CREATE POLICY mp_opt_controller_scope ON evidence_keys
    USING (
        mp_opt_rls_root()
        OR (
            role = 'processor'
            AND event_id = mp_opt_rls_event_id()
            AND controller_id = mp_opt_rls_controller_id()
        )
    )
    WITH CHECK (mp_opt_rls_root());

ALTER TABLE data_policy_acknowledgements ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_policy_acknowledgements FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_event_scope ON data_policy_acknowledgements;
CREATE POLICY mp_opt_event_scope ON data_policy_acknowledgements
    USING (
        mp_opt_rls_root() OR
        (event_id = mp_opt_rls_event_id() AND controller_id = mp_opt_rls_controller_id())
    )
    WITH CHECK (
        mp_opt_rls_root() OR
        (event_id = mp_opt_rls_event_id() AND controller_id = mp_opt_rls_controller_id())
    );

ALTER TABLE account_processing_consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_processing_consents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_event_scope ON account_processing_consents;
CREATE POLICY mp_opt_event_scope ON account_processing_consents
    USING (
        mp_opt_rls_root() OR
        (event_id = mp_opt_rls_event_id() AND controller_id = mp_opt_rls_controller_id())
    )
    WITH CHECK (
        mp_opt_rls_root() OR
        (event_id = mp_opt_rls_event_id() AND controller_id = mp_opt_rls_controller_id())
    );

ALTER TABLE deletion_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE deletion_cases FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_event_scope ON deletion_cases;
CREATE POLICY mp_opt_event_scope ON deletion_cases
    USING (
        mp_opt_rls_root() OR
        (event_id = mp_opt_rls_event_id() AND controller_id = mp_opt_rls_controller_id())
    )
    WITH CHECK (
        mp_opt_rls_root() OR
        (event_id = mp_opt_rls_event_id() AND controller_id = mp_opt_rls_controller_id())
    );

ALTER TABLE controller_evidence_chain_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE controller_evidence_chain_states FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_root_only ON controller_evidence_chain_states;
DROP POLICY IF EXISTS mp_opt_controller_evidence_writer ON controller_evidence_chain_states;
CREATE POLICY mp_opt_controller_evidence_writer ON controller_evidence_chain_states
    USING (
        mp_opt_rls_root() OR (
            mp_opt_rls_scope() = 'controller_evidence_writer'
            AND controller_id = mp_opt_rls_controller_id()
        )
    )
    WITH CHECK (
        mp_opt_rls_root() OR (
            mp_opt_rls_scope() = 'controller_evidence_writer'
            AND controller_id = mp_opt_rls_controller_id()
        )
    );

ALTER TABLE evidence_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_operations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mp_opt_root_only ON evidence_operations;
DROP POLICY IF EXISTS mp_opt_controller_evidence_writer ON evidence_operations;
CREATE POLICY mp_opt_controller_evidence_writer ON evidence_operations
    USING (
        mp_opt_rls_root() OR (
            mp_opt_rls_scope() = 'controller_evidence_writer'
            AND chain_scope = 'controller'
            AND controller_id = mp_opt_rls_controller_id()
            AND (event_id IS NULL OR event_id = mp_opt_rls_event_id())
        )
    )
    WITH CHECK (
        mp_opt_rls_root() OR (
            mp_opt_rls_scope() = 'controller_evidence_writer'
            AND chain_scope = 'controller'
            AND controller_id = mp_opt_rls_controller_id()
            AND (event_id IS NULL OR event_id = mp_opt_rls_event_id())
        )
    );

DO $deletion_child_rls$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'deletion_subject_scopes', 'desktop_deletion_work_orders',
        'deletion_required_processors', 'deletion_checklist_approvals',
        'deletion_approval_challenges'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS mp_opt_event_scope ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY mp_opt_event_scope ON %I USING '
            '(mp_opt_rls_root() OR EXISTS (SELECT 1 FROM deletion_cases c '
            'WHERE c.id = case_id AND c.event_id = mp_opt_rls_event_id() AND '
            'c.controller_id = mp_opt_rls_controller_id())) WITH CHECK '
            '(mp_opt_rls_root() OR EXISTS (SELECT 1 FROM deletion_cases c '
            'WHERE c.id = case_id AND c.event_id = mp_opt_rls_event_id() AND '
            'c.controller_id = mp_opt_rls_controller_id()))', table_name
        );
    END LOOP;
END
$deletion_child_rls$;

INSERT INTO server_settings (key, value)
VALUES ('tenancy_mode', 'single-controller')
ON CONFLICT (key) DO NOTHING;

COMMIT;
