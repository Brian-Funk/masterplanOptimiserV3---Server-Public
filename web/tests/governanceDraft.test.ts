import { describe, expect, it } from "vitest";

import {
  codesFromText,
  createInitialStructured,
  createSuggestedSummaries,
  type RuntimeSettings,
} from "@/lib/governanceDraft";

const settings: RuntimeSettings = {
  event_purge_grace_days: { value: 42, default: 90, label: "Event purge grace", unit: "days" },
  audit_log_retention_days: { value: 120, default: 90, label: "Audit retention", unit: "days" },
  offline_access_ttl_hours: { value: 12, default: 24, label: "Offline access", unit: "hours" },
};

describe("governance draft guidance", () => {
  it("prefills only configured technical retention values and leaves controller periods undecided", () => {
    const draft = createInitialStructured(
      { smtp_enabled: true, push_enabled: false, ha_enabled: false, dns_mode: "dns_only" },
      settings,
    );

    expect(draft.retention.event_grace_days).toBe(42);
    expect(draft.retention.audit_retention_days).toBe(120);
    expect(draft.retention.browser_cache_expiry_hours).toBe(12);
    expect(draft.retention.live_retention_days).toBeNull();
    expect(draft.retention.backup_retention_days).toBeNull();
    expect(draft.retention.receipt_retention_days).toBeNull();
    expect(draft.hosting_countries).toEqual([]);
    expect(draft.processors).toEqual([]);
    expect(draft.optional_features.smtp_enabled).toBe(true);
    expect(draft.optional_features.smtp_provider_code).toBeNull();
  });

  it("marks suggested public wording as an incomplete controller draft", () => {
    const summaries = createSuggestedSummaries(settings);
    expect(summaries.retention_summary).toContain("TODO:");
    expect(summaries.retention_summary).toContain("42 day(s)");
    expect(summaries.processor_summary).toContain("countries");
    expect(summaries.rights_summary).toContain("identity checks");
  });

  it("normalises country-code lists without inventing values", () => {
    expect(codesFromText("ch, DE, ch,  us ")).toEqual(["CH", "DE", "US"]);
    expect(codesFromText("")).toEqual([]);
  });
});
