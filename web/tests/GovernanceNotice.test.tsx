import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GovernanceNotice } from "@/components/GovernanceNotice";

const notice = {
  configured: true,
  version: 3,
  published_at: "2026-07-31T12:00:00Z",
  controller_legal_name: "Synthetic Controller",
  controller_postal_address: "Controller Street 1",
  controller_country: "CH",
  privacy_contact_email: "privacy@synthetic-controller.ch",
  supervisory_authority_name: "Synthetic Authority",
  supervisory_authority_url: "https://authority.invalid/",
  processor_summary: "Controller-supplied processor summary.",
  retention_summary: "Controller-supplied retention summary.",
  rights_summary: "Contact the controller.",
  terms_summary: "Authorised operational use only.",
  permitted_data: { purpose: "Operational scheduling", allowed: ["names"], unsupported: ["health"] },
  storage: {
    tracking: false,
    session_cookie: "Strictly necessary session cookie.",
    csrf_cookie: "Strictly necessary request-integrity cookie.",
    session_metadata: "Pseudonymous session metadata.",
    application_shell: "Static application shell.",
    preferences: "Non-sensitive preferences.",
    tab_state: "Temporary activation state.",
  },
  authentication: "Passkey public-key material only.",
  retention: { event_grace_days: 7, backup_retention_days: 30 },
  processors: [{
    provider_code: "vps",
    display_name: "Synthetic VPS",
    service: "Hosting",
    hosting_countries: ["CH"],
    public_notice_summary: "Hosts the instance in Switzerland.",
  }],
  feature_disclosures: [
    { code: "manual_activation", text: "Activation links are distributed manually." },
    { code: "dns_only_routing", text: "Application HTTPS is established directly with the selected server." },
  ],
  rights_request_url: "https://synthetic-controller.invalid/rights",
  incident_contact_email: "incident@synthetic-controller.ch",
};

describe("GovernanceNotice", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders only disclosures present in the immutable published payload", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => notice }));
    render(<GovernanceNotice section="privacy" />);

    expect(await screen.findByText("Synthetic Controller")).toBeInTheDocument();
    expect(screen.getByText(/Activation links are distributed manually/)).toBeInTheDocument();
    expect(screen.getByText(/Application HTTPS is established directly/)).toBeInTheDocument();
    expect(screen.queryByText(/Web Push/)).not.toBeInTheDocument();
    expect(screen.queryByText(/IndexedDB/)).not.toBeInTheDocument();
  });

  it("renders controller terms instead of hardcoded maintainer-specific terms", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => notice }));
    render(<GovernanceNotice section="terms" />);

    expect(await screen.findByText("Authorised operational use only.")).toBeInTheDocument();
    expect(screen.queryByText(/All rights reserved/)).not.toBeInTheDocument();
  });
});
