import { describe, expect, it } from "vitest";

import {
  GOVERNANCE_CONFIGURATION_FORMAT,
  GOVERNANCE_CONFIGURATION_VERSION,
  type GovernanceFormState,
  createGovernanceConfiguration,
  governanceConfigurationFilename,
  parseGovernanceConfiguration,
  serializeGovernanceConfiguration,
} from "@/lib/governanceConfig";
import { createInitialStructured } from "@/lib/governanceDraft";

const form: GovernanceFormState = {
  controller_type: "organisation",
  controller_legal_name: "Synthetic Controller Cooperative",
  controller_postal_address: "1 Example Way\n8000 Example City",
  controller_country: "CH",
  privacy_contact_email: "privacy@example.com",
  privacy_contact_phone: "",
  dpo_contact: "",
  supervisory_authority_name: "Synthetic Supervisory Authority",
  supervisory_authority_url: "https://authority.example",
  default_locale: "en",
  processor_summary: "Synthetic providers are listed in the structured configuration.",
  retention_summary: "Synthetic operational data follows the declared test periods.",
  rights_summary: "Synthetic requests are submitted through the test contact.",
  terms_summary: "This instance is restricted to authorised synthetic testing.",
};

const runtime = { smtp_enabled: true, push_enabled: false, ha_enabled: false, dns_mode: "dns_only" as const };

describe("governance configuration files", () => {
  it("round-trips every editable scalar and structured entry in a versioned envelope", () => {
    const structured = createInitialStructured(runtime, {});
    structured.instance_name = "Synthetic Privacy Lab";
    structured.hosting_countries = ["CH"];
    const exportedAt = new Date("2031-04-05T12:30:00.000Z");

    const file = createGovernanceConfiguration(form, structured, exportedAt);
    expect(file.format).toBe(GOVERNANCE_CONFIGURATION_FORMAT);
    expect(file.version).toBe(GOVERNANCE_CONFIGURATION_VERSION);
    expect(file.exported_at).toBe("2031-04-05T12:30:00.000Z");
    expect(file).not.toHaveProperty("confirmations");
    expect(file).not.toHaveProperty("private_key");

    const imported = parseGovernanceConfiguration(serializeGovernanceConfiguration(form, structured, exportedAt), structured);
    expect(imported.form).toEqual(form);
    expect(imported.structured).toEqual(structured);
  });

  it("keeps deployment-derived runtime facts when importing a reusable configuration", () => {
    const source = createInitialStructured(
      { smtp_enabled: false, push_enabled: true, ha_enabled: true, dns_mode: "dns_only" },
      {},
    );
    source.optional_features.smtp_provider_code = "synthetic_mail";
    source.optional_features.push_provider_codes = ["synthetic_push"];
    const current = createInitialStructured(runtime, {});

    const imported = parseGovernanceConfiguration(serializeGovernanceConfiguration(form, source), current);

    expect(imported.structured.optional_features.smtp_enabled).toBe(true);
    expect(imported.structured.optional_features.push_enabled).toBe(false);
    expect(imported.structured.optional_features.ha_enabled).toBe(false);
    expect(imported.structured.optional_features.dns_mode).toBe("dns_only");
    expect(imported.structured.optional_features.smtp_provider_code).toBe("synthetic_mail");
    expect(imported.structured.optional_features.push_provider_codes).toEqual(["synthetic_push"]);
  });

  it("rejects invalid JSON, unknown formats, unknown versions and malformed drafts", () => {
    const current = createInitialStructured(runtime, {});
    expect(() => parseGovernanceConfiguration("not json", current)).toThrow("not valid JSON");
    expect(() => parseGovernanceConfiguration(JSON.stringify({ format: "other", version: 1 }), current)).toThrow("Unsupported governance file format");
    expect(() => parseGovernanceConfiguration(JSON.stringify({ format: GOVERNANCE_CONFIGURATION_FORMAT, version: 99 }), current)).toThrow("Unsupported governance file version");

    const malformed = createGovernanceConfiguration(form, current);
    (malformed.draft.structured.data_categories[0] as { sensitive_data_supported: boolean }).sensitive_data_supported = true;
    expect(() => parseGovernanceConfiguration(JSON.stringify(malformed), current)).toThrow("sensitive_data_supported must remain false");
  });

  it("normalises nullable optional contact fields for the editor", () => {
    const current = createInitialStructured(runtime, {});
    const file = createGovernanceConfiguration(form, current) as unknown as {
      draft: Record<string, unknown>;
    };
    file.draft.privacy_contact_phone = null;
    file.draft.dpo_contact = null;

    const imported = parseGovernanceConfiguration(JSON.stringify(file), current);
    expect(imported.form.privacy_contact_phone).toBe("");
    expect(imported.form.dpo_contact).toBe("");
  });

  it("creates a predictable safe filename", () => {
    expect(governanceConfigurationFilename(" Synthetic Privacy Lab / CH ")).toBe("masterplan-governance-synthetic-privacy-lab-ch.json");
    expect(governanceConfigurationFilename("***")).toBe("masterplan-governance-draft.json");
  });
});
