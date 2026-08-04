import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

const { mockHardNavigate, mockStartRegistration } = vi.hoisted(() => ({
  mockHardNavigate: vi.fn(), mockStartRegistration: vi.fn(),
}));
vi.mock("@/lib/hardNavigation", () => ({ hardNavigate: mockHardNavigate }));
vi.mock("@/lib/environment", () => ({ getApiUrl: () => "https://api.test" }));
vi.mock("@simplewebauthn/browser", () => ({ startRegistration: mockStartRegistration }));
vi.mock("@/contexts/ThemeContext", () => ({ useTheme: () => ({ theme: "light", toggleTheme: vi.fn() }) }));
vi.mock("@/lib/brand", () => ({ BRAND: { color1: "#2563eb", color2: "#7c3aed" } }));
vi.mock("lucide-react", () => ({ Moon: (props: object) => React.createElement("svg", props), Sun: (props: object) => React.createElement("svg", props) }));

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);
import BootstrapPage from "@/app/bootstrap/page";

const status = (overrides: object = {}) => ({
  needs_bootstrap: true, bootstrap_configured: true, stage: "passkey",
  policy_version: "2026-07-30", policy_sha256: "a".repeat(64),
  policy_text: "Synthetic permitted-data policy.", ...overrides,
});

describe("BootstrapPage", () => {
  beforeEach(() => { mockFetch.mockReset(); mockHardNavigate.mockReset(); mockStartRegistration.mockReset(); });

  it("shows the one-time root passkey gate", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => status() });
    render(<BootstrapPage />);
    expect(await screen.findByRole("button", { name: /register root passkey/i })).toBeDisabled();
    expect(screen.getByText(/bootstrap code is permanently retired/i)).toBeInTheDocument();
  });

  it("sends an already registered root to normal sign-in", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => status({ needs_bootstrap: false, bootstrap_configured: false, stage: "setup" }) });
    render(<BootstrapPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: /continue to sign in/i }));
    expect(mockHardNavigate).toHaveBeenCalledWith("/login");
  });

  it("retires bootstrap through registration, exchanges the restricted session, and opens setup", async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => status() })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ options: JSON.stringify({ challenge: "abc" }), ceremony_id: "ceremony" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ exchange_code: "exchange-code-for-setup" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ commissioning_required: true, commissioning_stage: "recovery" }) });
    mockStartRegistration.mockResolvedValueOnce({ id: "credential" });
    render(<BootstrapPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox"));
    await user.type(screen.getByLabelText(/bootstrap code/i), "b".repeat(32));
    await user.click(screen.getByRole("button", { name: /register root passkey/i }));
    await waitFor(() => expect(mockHardNavigate).toHaveBeenCalledWith("/setup"));
    expect(JSON.parse(mockFetch.mock.calls[2][1].body)).toMatchObject({ ceremony_id: "ceremony", credential: { id: "credential" } });
    expect(JSON.parse(mockFetch.mock.calls[3][1].body)).toEqual({ code: "exchange-code-for-setup" });
  });

  it("returns to the gate when the passkey prompt is cancelled", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => status() }).mockResolvedValueOnce({ ok: true, json: async () => ({ options: "{}", ceremony_id: "ceremony" }) });
    const cancelled = new Error("cancelled"); cancelled.name = "NotAllowedError"; mockStartRegistration.mockRejectedValueOnce(cancelled);
    render(<BootstrapPage />); const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox")); await user.type(screen.getByLabelText(/bootstrap code/i), "b".repeat(32)); await user.click(screen.getByRole("button", { name: /register root passkey/i }));
    expect(await screen.findByRole("button", { name: /register root passkey/i })).toBeInTheDocument();
  });

  it("shows a calm retry when status is unavailable", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false }); render(<BootstrapPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/failed to check bootstrap status/i);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});
