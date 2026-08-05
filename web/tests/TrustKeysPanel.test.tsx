import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockApiFetch = vi.hoisted(() => vi.fn());
const mockStartAuthentication = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ apiFetch: mockApiFetch }));
vi.mock("@/lib/reauth", () => ({ withReauth: (operation: () => unknown) => operation() }));
vi.mock("@simplewebauthn/browser", () => ({ startAuthentication: mockStartAuthentication }));

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("TrustKeysPanel", () => {
  beforeEach(() => {
    mockApiFetch.mockReset();
    mockStartAuthentication.mockReset();
    mockStartAuthentication.mockResolvedValue({ id: "credential" });
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/v1/admin/evidence/trust-keys") return json([{
        instance_id: "instance-example", entity_id: "ctl-example0001", key_id: "ek-controller000001", role: "controller",
        public_key_sha256: "c".repeat(64), validity_status: "active", created_at: null,
        activated_at: "2026-08-03T19:00:00Z", revoked_at: null, supersedes_key_id: null,
        event_ref: null, event_name: null, display_label: null, trust_establishment_sha256: "d".repeat(64),
      }, {
        instance_id: "instance-example",
        entity_id: "prc-example0001", key_id: "ek-1234567890abcdef", role: "processor",
        public_key_sha256: "a".repeat(64), validity_status: "active", created_at: null,
        activated_at: "2026-08-03T20:00:00Z", revoked_at: null, supersedes_key_id: null,
        event_ref: "event-example", event_name: "Synthetic Event", display_label: "Primary workstation", trust_establishment_sha256: null,
      }]);
      if (path === "/api/v1/admin/evidence/trust-keys/pending-enrolments") return json([{
        challenge_id: "challenge-example", event_ref: "event-example", event_name: "Synthetic Event",
        entity_id: "prc-pending0001", display_label: "Backup workstation", key_id: "ek-fedcba0987654321",
        public_key_sha256: "b".repeat(64), purpose: "register", expires_at: "2026-08-03T20:10:00Z",
      }]);
      if (path === "/api/v1/admin/evidence/trust-keys/archive-trust") return json({
        ready: false, message: "Select the active controller key once to authorise portable evidence archives.",
      });
      if (path.endsWith("/root-authorisation/begin")) return json({ options: JSON.stringify({ challenge: "example" }), ceremony_id: "ceremony-example" });
      if (path.endsWith("/root-authorisation/complete")) return json({ status: "active" });
      throw new Error(`Unexpected path: ${path}`);
    });
  });

  it("explains separated trust scopes and activates one readable event assignment", async () => {
    const { TrustKeysPanel } = await import("@/components/TrustKeysPanel");
    render(<TrustKeysPanel />);

    expect(await screen.findByText("Controller trust")).toBeInTheDocument();
    expect(screen.getByText("Root passkey")).toBeInTheDocument();
    expect(screen.getByText("Instance evidence key")).toBeInTheDocument();
    expect(screen.getByText("Primary workstation")).toBeInTheDocument();
    expect(screen.getByText("Backup workstation")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Approve this event assignment" }));
    await waitFor(() => expect(mockStartAuthentication).toHaveBeenCalled());
    await waitFor(() => expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/v1/admin/evidence/trust-keys/challenge-example/root-authorisation/complete",
      { method: "POST", body: JSON.stringify({ ceremony_id: "ceremony-example", credential: { id: "credential" } }) },
    ));
  });

  it("shows controller trust as established by registration without another import", async () => {
    const { TrustKeysPanel } = await import("@/components/TrustKeysPanel");
    render(<TrustKeysPanel />);

    expect(await screen.findByText(/identity and possession were established during registration/i)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /initial controller trust/i })).not.toBeInTheDocument();
    expect(mockApiFetch.mock.calls.some(([path]) => String(path).includes("statements/import"))).toBe(false);
  });
});
