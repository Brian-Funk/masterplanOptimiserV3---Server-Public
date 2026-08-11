import {
  type DataCategory,
  type GovernanceStructured,
  type ProcessingPurpose,
  type ProcessorEntry,
  type PurposeCode,
} from "@/lib/governanceDraft";

export const GOVERNANCE_CONFIGURATION_FORMAT = "masterplan-governance-draft";
export const GOVERNANCE_CONFIGURATION_VERSION = 1;
export const GOVERNANCE_CONFIGURATION_MAX_BYTES = 1024 * 1024;

export type GovernanceFormState = {
  controller_type: "organisation" | "individual";
  controller_legal_name: string;
  controller_postal_address: string;
  controller_country: string;
  privacy_contact_email: string;
  dpo_contact: string;
  supervisory_authority_name: string;
  supervisory_authority_url: string;
  default_locale: string;
  processor_summary: string;
  retention_summary: string;
  rights_summary: string;
  terms_summary: string;
};

export type GovernanceConfigurationFile = {
  format: typeof GOVERNANCE_CONFIGURATION_FORMAT;
  version: typeof GOVERNANCE_CONFIGURATION_VERSION;
  exported_at: string;
  draft: GovernanceFormState & { structured: GovernanceStructured };
};

const PURPOSE_CODES = new Set<PurposeCode>([
  "event_scheduling",
  "account_authentication",
  "activation_email",
  "security_audit",
  "offline_schedule",
  "push_notifications",
  "public_schedule",
  "backup_and_recovery",
  "support",
]);

const FORM_STRING_FIELDS: Array<Exclude<keyof GovernanceFormState, "controller_type">> = [
  "controller_legal_name",
  "controller_postal_address",
  "controller_country",
  "privacy_contact_email",
  "dpo_contact",
  "supervisory_authority_name",
  "supervisory_authority_url",
  "default_locale",
  "processor_summary",
  "retention_summary",
  "rights_summary",
  "terms_summary",
];

type JsonObject = Record<string, unknown>;

function record(value: unknown, path: string): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${path} must be an object.`);
  }
  return value as JsonObject;
}

function stringValue(value: unknown, path: string): string {
  if (typeof value !== "string") throw new Error(`${path} must be text.`);
  return value;
}

function optionalString(value: unknown, path: string): string | null {
  if (value === null) return null;
  return stringValue(value, path);
}

function booleanValue(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${path} must be true or false.`);
  return value;
}

function optionalBoolean(value: unknown, path: string): boolean | null {
  if (value === null) return null;
  return booleanValue(value, path);
}

function optionalNumber(value: unknown, path: string): number | null {
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isInteger(value)) throw new Error(`${path} must be a whole number or null.`);
  return value;
}

function arrayValue(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${path} must be a list.`);
  return value;
}

function stringArray(value: unknown, path: string): string[] {
  return arrayValue(value, path).map((item, index) => stringValue(item, `${path}[${index}]`));
}

function enumValue<T extends string>(value: unknown, allowed: readonly T[], path: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    throw new Error(`${path} has an unsupported value.`);
  }
  return value as T;
}

function purposeCodes(value: unknown, path: string): PurposeCode[] {
  return stringArray(value, path).map((item, index) => {
    if (!PURPOSE_CODES.has(item as PurposeCode)) throw new Error(`${path}[${index}] has an unsupported purpose code.`);
    return item as PurposeCode;
  });
}

function processingPurpose(value: unknown, index: number): ProcessingPurpose {
  const path = `draft.structured.processing_purposes[${index}]`;
  const item = record(value, path);
  const purposeCode = stringValue(item.purpose_code, `${path}.purpose_code`);
  if (!PURPOSE_CODES.has(purposeCode as PurposeCode)) throw new Error(`${path}.purpose_code is unsupported.`);
  return {
    purpose_code: purposeCode as PurposeCode,
    enabled: booleanValue(item.enabled, `${path}.enabled`),
    description: stringValue(item.description, `${path}.description`),
    gdpr_legal_basis: optionalString(item.gdpr_legal_basis, `${path}.gdpr_legal_basis`),
    swiss_justification_or_basis: optionalString(item.swiss_justification_or_basis, `${path}.swiss_justification_or_basis`),
    legitimate_interest_summary: optionalString(item.legitimate_interest_summary, `${path}.legitimate_interest_summary`),
    required_or_optional: enumValue(item.required_or_optional, ["required", "optional"], `${path}.required_or_optional`),
    withdrawal_effect: optionalString(item.withdrawal_effect, `${path}.withdrawal_effect`),
  };
}

function dataCategory(value: unknown, index: number): DataCategory {
  const path = `draft.structured.data_categories[${index}]`;
  const item = record(value, path);
  if (item.sensitive_data_supported !== false) throw new Error(`${path}.sensitive_data_supported must remain false.`);
  return {
    category_code: stringValue(item.category_code, `${path}.category_code`),
    display_name: stringValue(item.display_name, `${path}.display_name`),
    enabled: booleanValue(item.enabled, `${path}.enabled`),
    required_or_optional: enumValue(item.required_or_optional, ["required", "optional"], `${path}.required_or_optional`),
    visibility: enumValue(item.visibility, ["root", "organiser", "participant", "public"], `${path}.visibility`),
    source: stringValue(item.source, `${path}.source`),
    purpose_codes: purposeCodes(item.purpose_codes, `${path}.purpose_codes`),
    retention_policy_code: stringValue(item.retention_policy_code, `${path}.retention_policy_code`),
    sensitive_data_supported: false,
  };
}

function processor(value: unknown, index: number): ProcessorEntry {
  const path = `draft.structured.processors[${index}]`;
  const item = record(value, path);
  return {
    provider_code: stringValue(item.provider_code, `${path}.provider_code`),
    display_name: stringValue(item.display_name, `${path}.display_name`),
    service: stringValue(item.service, `${path}.service`),
    role: enumValue(item.role, ["processor", "independent_controller", "infrastructure_provider"], `${path}.role`),
    purpose_codes: purposeCodes(item.purpose_codes, `${path}.purpose_codes`),
    data_categories: stringArray(item.data_categories, `${path}.data_categories`),
    hosting_countries: stringArray(item.hosting_countries, `${path}.hosting_countries`),
    support_access_countries: stringArray(item.support_access_countries, `${path}.support_access_countries`),
    dpa_status: enumValue(item.dpa_status, ["accepted", "pending", "not_required", "unknown"], `${path}.dpa_status`),
    dpa_version: optionalString(item.dpa_version, `${path}.dpa_version`),
    subprocessor_url: optionalString(item.subprocessor_url, `${path}.subprocessor_url`),
    transfer_mechanism: optionalString(item.transfer_mechanism, `${path}.transfer_mechanism`),
    public_notice_summary: stringValue(item.public_notice_summary, `${path}.public_notice_summary`),
    internal_notes_reference: optionalString(item.internal_notes_reference, `${path}.internal_notes_reference`),
    review_due_at: optionalString(item.review_due_at, `${path}.review_due_at`),
    enabled: booleanValue(item.enabled, `${path}.enabled`),
  };
}

function structuredValue(value: unknown, current: GovernanceStructured): GovernanceStructured {
  const item = record(value, "draft.structured");
  const retention = record(item.retention, "draft.structured.retention");
  const features = record(item.optional_features, "draft.structured.optional_features");

  return {
    instance_name: stringValue(item.instance_name, "draft.structured.instance_name"),
    dpo_name_or_role: optionalString(item.dpo_name_or_role, "draft.structured.dpo_name_or_role"),
    eu_representative: optionalString(item.eu_representative, "draft.structured.eu_representative"),
    swiss_representative: optionalString(item.swiss_representative, "draft.structured.swiss_representative"),
    supported_locales: stringArray(item.supported_locales, "draft.structured.supported_locales"),
    jurisdiction_scope: stringValue(item.jurisdiction_scope, "draft.structured.jurisdiction_scope"),
    processing_purposes: arrayValue(item.processing_purposes, "draft.structured.processing_purposes").map(processingPurpose),
    data_categories: arrayValue(item.data_categories, "draft.structured.data_categories").map(dataCategory),
    processors: arrayValue(item.processors, "draft.structured.processors").map(processor),
    hosting_countries: stringArray(item.hosting_countries, "draft.structured.hosting_countries"),
    retention: {
      policy_code: stringValue(retention.policy_code, "draft.structured.retention.policy_code"),
      live_retention_days: optionalNumber(retention.live_retention_days, "draft.structured.retention.live_retention_days"),
      event_grace_days: optionalNumber(retention.event_grace_days, "draft.structured.retention.event_grace_days"),
      backup_retention_days: optionalNumber(retention.backup_retention_days, "draft.structured.retention.backup_retention_days"),
      audit_retention_days: optionalNumber(retention.audit_retention_days, "draft.structured.retention.audit_retention_days"),
      receipt_retention_days: optionalNumber(retention.receipt_retention_days, "draft.structured.retention.receipt_retention_days"),
      browser_cache_expiry_hours: optionalNumber(retention.browser_cache_expiry_hours, "draft.structured.retention.browser_cache_expiry_hours"),
      automatic_purge_enabled: optionalBoolean(retention.automatic_purge_enabled, "draft.structured.retention.automatic_purge_enabled"),
      legal_hold_supported: optionalBoolean(retention.legal_hold_supported, "draft.structured.retention.legal_hold_supported"),
    },
    optional_features: {
      smtp_enabled: current.optional_features.smtp_enabled,
      push_enabled: current.optional_features.push_enabled,
      ha_enabled: current.optional_features.ha_enabled,
      dns_mode: current.optional_features.dns_mode,
      smtp_provider_code: optionalString(features.smtp_provider_code, "draft.structured.optional_features.smtp_provider_code"),
      push_provider_codes: stringArray(features.push_provider_codes, "draft.structured.optional_features.push_provider_codes"),
      offline_schedule_enabled: booleanValue(features.offline_schedule_enabled, "draft.structured.optional_features.offline_schedule_enabled"),
      public_schedule_enabled: booleanValue(features.public_schedule_enabled, "draft.structured.optional_features.public_schedule_enabled"),
      external_support_enabled: booleanValue(features.external_support_enabled, "draft.structured.optional_features.external_support_enabled"),
      backup_storage_mode: enumValue(features.backup_storage_mode, ["manual_portable", "ssh_archive", "controller_managed"], "draft.structured.optional_features.backup_storage_mode"),
    },
    rights_request_url: optionalString(item.rights_request_url, "draft.structured.rights_request_url"),
    incident_contact_email: optionalString(item.incident_contact_email, "draft.structured.incident_contact_email"),
  };
}

export function createGovernanceConfiguration(
  form: GovernanceFormState,
  structured: GovernanceStructured,
  exportedAt = new Date(),
): GovernanceConfigurationFile {
  return {
    format: GOVERNANCE_CONFIGURATION_FORMAT,
    version: GOVERNANCE_CONFIGURATION_VERSION,
    exported_at: exportedAt.toISOString(),
    draft: { ...form, structured },
  };
}

export function serializeGovernanceConfiguration(
  form: GovernanceFormState,
  structured: GovernanceStructured,
  exportedAt = new Date(),
): string {
  return `${JSON.stringify(createGovernanceConfiguration(form, structured, exportedAt), null, 2)}\n`;
}

export function parseGovernanceConfiguration(
  source: string,
  currentStructured: GovernanceStructured,
): { form: GovernanceFormState; structured: GovernanceStructured } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(source);
  } catch {
    throw new Error("The selected file is not valid JSON.");
  }

  const envelope = record(parsed, "configuration");
  if (envelope.format !== GOVERNANCE_CONFIGURATION_FORMAT) {
    throw new Error(`Unsupported governance file format. Expected ${GOVERNANCE_CONFIGURATION_FORMAT}.`);
  }
  if (envelope.version !== GOVERNANCE_CONFIGURATION_VERSION) {
    throw new Error(`Unsupported governance file version. Expected version ${GOVERNANCE_CONFIGURATION_VERSION}.`);
  }
  stringValue(envelope.exported_at, "exported_at");
  const draft = record(envelope.draft, "draft");
  if (Object.prototype.hasOwnProperty.call(draft, "privacy_contact_phone")) {
    throw new Error(
      "draft.privacy_contact_phone is retired. Use draft.privacy_contact_email instead.",
    );
  }
  const controllerType = enumValue(draft.controller_type, ["organisation", "individual"], "draft.controller_type");
  const form = { controller_type: controllerType } as GovernanceFormState;
  for (const field of FORM_STRING_FIELDS) {
    const value = draft[field];
    if (field === "dpo_contact" && value === null) form[field] = "";
    else form[field] = stringValue(value, `draft.${field}`);
  }

  return { form, structured: structuredValue(draft.structured, currentStructured) };
}

export function governanceConfigurationFilename(instanceName: string): string {
  const slug = instanceName
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return `masterplan-governance-${slug || "draft"}.json`;
}
