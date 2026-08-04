import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GovernanceAdminPage from "@/app/admin/governance/page";
import { createInitialStructured } from "@/lib/governanceDraft";
import { serializeGovernanceConfiguration, type GovernanceFormState } from "@/lib/governanceConfig";

const mockApiFetch = vi.hoisted(() => vi.fn());
const mockUseAuth = vi.hoisted(() => vi.fn());
const mockRouterPush = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ apiFetch: mockApiFetch }));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: mockUseAuth }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mockRouterPush }) }));
vi.mock("@/components/Logo", () => ({ Logo: () => <div>Logo</div> }));
vi.mock("@/components/ThemeToggle", () => ({ ThemeToggle: () => null }));

const root = {
  id: 1,
  username: "root",
  display_name: "Root",
  is_root_admin: true,
  is_admin: true,
  is_issuer: false,
  can_edit: true,
  is_active: true,
  is_activated: true,
  event_id: null,
};

const importedForm: GovernanceFormState = {
  controller_type: "organisation",
  controller_legal_name: "Imported Synthetic Controller",
  controller_postal_address: "2 Fixture Street",
  controller_country: "CH",
  privacy_contact_email: "privacy@example.com",
  privacy_contact_phone: "",
  dpo_contact: "",
  supervisory_authority_name: "Synthetic Authority",
  supervisory_authority_url: "https://authority.example",
  default_locale: "en",
  processor_summary: "Imported processor summary",
  retention_summary: "Imported retention summary",
  rights_summary: "Imported rights summary",
  terms_summary: "Imported terms summary",
};

function jsonResponse(data: unknown): Response {
  return { ok: true, status: 200, json: async () => data } as Response;
}

describe("governance configuration import and export", () => {
  beforeEach(() => {
    mockApiFetch.mockReset();
    mockUseAuth.mockReturnValue({ user: root, isLoading: false });
    mockRouterPush.mockReset();
    mockApiFetch.mockImplementation((url: string) => {
      if (url === "/api/v1/admin/governance") {
        return Promise.resolve(jsonResponse({
          runtime_features: { smtp_enabled: true, push_enabled: false, ha_enabled: false, dns_mode: "dns_only" },
          draft: null,
          published_version: null,
          preflight: { checks: [], ready: false },
        }));
      }
      if (url === "/api/v1/admin/settings") return Promise.resolve(jsonResponse({}));
      throw new Error(`Unexpected request: ${url}`);
    });
  });

  it("offers a versioned configuration download without treating it as evidence export", async () => {
    const user = userEvent.setup();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:governance-draft");
    const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    render(<GovernanceAdminPage />);

    await screen.findByRole("heading", { name: "Governance configuration file" });
    await user.click(screen.getByRole("button", { name: "Export current entries" }));

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:governance-draft");
    expect(screen.getByRole("status")).toHaveTextContent(/No key, signature, publication approval or passkey material is included/i);
    expect(screen.getByRole("link", { name: "Open Trust & keys" })).toHaveAttribute("href", "/admin/governance/trust");
  });

  it("imports all entries as an unsaved draft and does not call the save API", async () => {
    const user = userEvent.setup();
    const structured = createInitialStructured(
      { smtp_enabled: false, push_enabled: true, ha_enabled: true, dns_mode: "dns_only" },
      {},
    );
    structured.instance_name = "Imported Synthetic Instance";
    const file = new File(
      [serializeGovernanceConfiguration(importedForm, structured)],
      "synthetic-governance.json",
      { type: "application/json" },
    );
    render(<GovernanceAdminPage />);

    const input = await screen.findByLabelText("Choose governance configuration JSON");
    await user.upload(input, file);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/imported into an unsaved draft/i));
    expect(screen.getByLabelText("Legal name")).toHaveValue("Imported Synthetic Controller");
    expect(screen.getByLabelText("Public instance name")).toHaveValue("Imported Synthetic Instance");
    expect(mockApiFetch).toHaveBeenCalledTimes(2);
  });

  it("rejects malformed imports without replacing the editor", async () => {
    const user = userEvent.setup();
    const file = new File(["not json"], "broken.json", { type: "application/json" });
    render(<GovernanceAdminPage />);

    const input = await screen.findByLabelText("Choose governance configuration JSON");
    await user.upload(input, file);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/not valid JSON/i));
    expect(screen.getByLabelText("Legal name")).toHaveValue("");
    expect(mockApiFetch).toHaveBeenCalledTimes(2);
  });

  it("opens root-only saved-draft documents after a successful exact preview", async () => {
    const user = userEvent.setup();
    mockApiFetch.mockImplementation((url: string) => {
      if (url === "/api/v1/admin/governance") {
        return Promise.resolve(jsonResponse({
          runtime_features: { smtp_enabled: true, push_enabled: false, ha_enabled: false, dns_mode: "dns_only" },
          draft: null,
          published_version: null,
          preflight: { checks: [], ready: false },
        }));
      }
      if (url === "/api/v1/admin/settings") return Promise.resolve(jsonResponse({}));
      if (url === "/api/v1/admin/governance/preview") {
        return Promise.resolve(jsonResponse({
          preflight: { checks: [{ code: "controller", status: "ready", message: "Ready" }], ready: true },
          diff: { changes: [{ path: "controller_legal_name" }], material_change: true },
          published_version: null,
        }));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    render(<GovernanceAdminPage />);

    await user.click(await screen.findByRole("button", { name: "Generate exact preview and diff" }));

    const privacy = await screen.findByRole("link", { name: "Privacy draft preview" });
    expect(privacy).toHaveAttribute("href", "/api/v1/admin/governance/preview/privacy.html");
    expect(privacy).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("status")).toHaveTextContent(/review every public page before publishing/i);
  });
});
