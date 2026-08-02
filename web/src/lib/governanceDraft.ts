export type RuntimeFeatures = {
  smtp_enabled: boolean;
  push_enabled: boolean;
  ha_enabled: boolean;
  dns_mode: "dns_only";
};

export type RuntimeSetting = {
  value: number;
  default: number;
  label: string;
  unit: string;
};

export type RuntimeSettings = Record<string, RuntimeSetting>;

export type PurposeCode =
  | "event_scheduling"
  | "account_authentication"
  | "activation_email"
  | "security_audit"
  | "offline_schedule"
  | "push_notifications"
  | "public_schedule"
  | "backup_and_recovery"
  | "support";

export type ProcessingPurpose = {
  purpose_code: PurposeCode;
  enabled: boolean;
  description: string;
  gdpr_legal_basis: string | null;
  swiss_justification_or_basis: string | null;
  legitimate_interest_summary: string | null;
  required_or_optional: "required" | "optional";
  withdrawal_effect: string | null;
};

export type DataCategory = {
  category_code: string;
  display_name: string;
  enabled: boolean;
  required_or_optional: "required" | "optional";
  visibility: "root" | "organiser" | "participant" | "public";
  source: string;
  purpose_codes: PurposeCode[];
  retention_policy_code: string;
  sensitive_data_supported: false;
};

export type ProcessorEntry = {
  provider_code: string;
  display_name: string;
  service: string;
  role: "processor" | "independent_controller" | "infrastructure_provider";
  purpose_codes: PurposeCode[];
  data_categories: string[];
  hosting_countries: string[];
  support_access_countries: string[];
  dpa_status: "accepted" | "pending" | "not_required" | "unknown";
  dpa_version: string | null;
  subprocessor_url: string | null;
  transfer_mechanism: string | null;
  public_notice_summary: string;
  internal_notes_reference: string | null;
  review_due_at: string | null;
  enabled: boolean;
};

export type RetentionConfiguration = {
  policy_code: string;
  live_retention_days: number | null;
  event_grace_days: number | null;
  backup_retention_days: number | null;
  audit_retention_days: number | null;
  receipt_retention_days: number | null;
  browser_cache_expiry_hours: number | null;
  automatic_purge_enabled: boolean | null;
  legal_hold_supported: boolean | null;
};

export type GovernanceStructured = {
  instance_name: string;
  dpo_name_or_role: string | null;
  eu_representative: string | null;
  swiss_representative: string | null;
  supported_locales: string[];
  jurisdiction_scope: string;
  processing_purposes: ProcessingPurpose[];
  data_categories: DataCategory[];
  processors: ProcessorEntry[];
  hosting_countries: string[];
  retention: RetentionConfiguration;
  optional_features: RuntimeFeatures & {
    smtp_provider_code: string | null;
    push_provider_codes: string[];
    offline_schedule_enabled: boolean;
    public_schedule_enabled: boolean;
    external_support_enabled: boolean;
    backup_storage_mode: "manual_portable" | "ssh_archive" | "controller_managed";
  };
  rights_request_url: string | null;
  incident_contact_email: string | null;
};

export const PURPOSE_LABELS: Record<PurposeCode, string> = {
  event_scheduling: "Operational event scheduling",
  account_authentication: "Account authentication",
  activation_email: "Activation email",
  security_audit: "Security and audit logging",
  offline_schedule: "Offline schedule access",
  push_notifications: "Push notifications",
  public_schedule: "Public schedule publishing",
  backup_and_recovery: "Backup and recovery",
  support: "External support",
};

const PURPOSE_DESCRIPTIONS: Record<PurposeCode, string> = {
  event_scheduling: "Create, coordinate and publish operational event schedules.",
  account_authentication: "Authenticate authorised users with passkeys and maintain bounded sessions.",
  activation_email: "Deliver one-time account activation links through the controller-selected mail provider.",
  security_audit: "Detect misuse and retain proportionate security and accountability records.",
  offline_schedule: "Make a bounded schedule copy available on the authorised device.",
  push_notifications: "Notify authorised users about relevant operational schedule changes.",
  public_schedule: "Publish controller-selected schedule information without participant-only fields.",
  backup_and_recovery: "Create controller-managed encrypted recovery material and verify restoration readiness.",
  support: "Permit explicitly authorised, bounded support access when the controller enables it.",
};

function setting(settings: RuntimeSettings, key: string): number | null {
  const value = settings[key]?.value;
  return Number.isFinite(value) ? value : null;
}

export function createSuggestedSummaries(settings: RuntimeSettings) {
  const eventGrace = setting(settings, "event_purge_grace_days");
  const auditDays = setting(settings, "audit_log_retention_days");
  const offlineHours = setting(settings, "offline_access_ttl_hours");
  return {
    processor_summary:
      "TODO: Identify each enabled hosting, email, push, backup, calendar and support provider. State its role, service, countries, support access, transfer safeguards and agreement status.",
    retention_summary:
      `TODO: Describe the controller's live-record and backup periods. The current Server settings use ${eventGrace ?? "a configured"} day(s) of event-purge grace, ${auditDays ?? "a configured number of"} day(s) for audit logs, and ${offlineHours ?? "a configured number of"} hour(s) for offline browser access. Explain deletion, evidence-receipt, legal-hold and external-copy periods.`,
    rights_summary:
      "TODO: Explain how a person requests access, correction, restriction, export, objection or deletion; identify the contact channel, identity checks and expected response process.",
    terms_summary:
      "TODO: State who may use this instance, the authorised operational purposes, the prohibited sensitive-data boundary, account responsibilities and consequences of misuse.",
  };
}

export function createInitialStructured(
  runtime: RuntimeFeatures,
  settings: RuntimeSettings,
): GovernanceStructured {
  const purposes = (Object.keys(PURPOSE_LABELS) as PurposeCode[]).map((code) => ({
    purpose_code: code,
    enabled:
      ["event_scheduling", "account_authentication", "security_audit", "offline_schedule", "public_schedule", "backup_and_recovery"].includes(code)
      || (code === "activation_email" && runtime.smtp_enabled)
      || (code === "push_notifications" && runtime.push_enabled),
    description: PURPOSE_DESCRIPTIONS[code],
    gdpr_legal_basis: null,
    swiss_justification_or_basis: null,
    legitimate_interest_summary: null,
    required_or_optional: ["offline_schedule", "push_notifications", "public_schedule", "support"].includes(code) ? "optional" : "required",
    withdrawal_effect: null,
  })) as ProcessingPurpose[];

  return {
    instance_name: "TODO: Enter the public name of this deployment",
    dpo_name_or_role: null,
    eu_representative: null,
    swiss_representative: null,
    supported_locales: ["en"],
    jurisdiction_scope: "TODO: State the countries and legal regimes the controller determined apply to this deployment.",
    processing_purposes: purposes,
    data_categories: [
      {
        category_code: "operational_identity",
        display_name: "Names, necessary business contact details and operational roles",
        enabled: true,
        required_or_optional: "required",
        visibility: "participant",
        source: "Controller and authorised users",
        purpose_codes: ["event_scheduling"],
        retention_policy_code: "instance_default",
        sensitive_data_supported: false,
      },
      {
        category_code: "authentication_metadata",
        display_name: "Passkey public-key and bounded session metadata",
        enabled: true,
        required_or_optional: "required",
        visibility: "root",
        source: "Authorised user and Server",
        purpose_codes: ["account_authentication", "security_audit"],
        retention_policy_code: "instance_default",
        sensitive_data_supported: false,
      },
    ],
    processors: [],
    hosting_countries: [],
    retention: {
      policy_code: "instance_default",
      live_retention_days: null,
      event_grace_days: setting(settings, "event_purge_grace_days"),
      backup_retention_days: null,
      audit_retention_days: setting(settings, "audit_log_retention_days"),
      receipt_retention_days: null,
      browser_cache_expiry_hours: setting(settings, "offline_access_ttl_hours"),
      automatic_purge_enabled: true,
      legal_hold_supported: false,
    },
    optional_features: {
      ...runtime,
      smtp_provider_code: null,
      push_provider_codes: [],
      offline_schedule_enabled: true,
      public_schedule_enabled: true,
      external_support_enabled: false,
      backup_storage_mode: "manual_portable",
    },
    rights_request_url: null,
    incident_contact_email: null,
  };
}

export function codesFromText(value: string): string[] {
  return [...new Set(value.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean))];
}

export function codesToText(values: string[]): string {
  return values.join(", ");
}
