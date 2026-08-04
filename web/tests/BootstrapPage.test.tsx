/**
 * Tests for BootstrapPage - initial passkey registration flow.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

// Mock next/navigation
const mockPush = vi.fn();
const { mockHardNavigate } = vi.hoisted(() => ({
  mockHardNavigate: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

vi.mock("@/lib/hardNavigation", () => ({
  hardNavigate: mockHardNavigate,
}));

// Mock environment
vi.mock("@/lib/environment", () => ({
  getApiUrl: () => "https://api.test",
}));

// Mock ThemeContext
vi.mock("@/contexts/ThemeContext", () => ({
  useTheme: () => ({ theme: "light", toggleTheme: vi.fn() }),
}));

// Mock brand
vi.mock("@/lib/brand", () => ({
  BRAND: { color1: "#2563eb", color2: "#7c3aed" },
}));

// Mock lucide-react
vi.mock("lucide-react", () => ({
  Moon: (props: Record<string, unknown>) =>
    React.createElement("svg", { ...props }),
  Sun: (props: Record<string, unknown>) =>
    React.createElement("svg", { ...props }),
}));

// Mock @simplewebauthn/browser
const mockStartRegistration = vi.fn();
vi.mock("@simplewebauthn/browser", () => ({
  startRegistration: (...args: unknown[]) => mockStartRegistration(...args),
}));

const mockGenerateAgeRecoveryIdentity = vi.fn();
vi.mock("@/lib/ageIdentity", () => ({
  generateAgeRecoveryIdentity: () => mockGenerateAgeRecoveryIdentity(),
}));

// Mock fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

import BootstrapPage from "@/app/bootstrap/page";

beforeEach(() => {
  mockPush.mockReset();
  mockHardNavigate.mockReset();
  mockFetch.mockReset();
  mockStartRegistration.mockReset();
  mockGenerateAgeRecoveryIdentity.mockReset();
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:recovery") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
});

describe("BootstrapPage", () => {
  it("shows checking status initially", () => {
    mockFetch.mockImplementation(() => new Promise(() => {}));
    render(<BootstrapPage />);
    expect(screen.getByText("Checking setup status...")).toBeInTheDocument();
  });

  it("shows register button when bootstrap is needed", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ needs_bootstrap: true }),
    });

    render(<BootstrapPage />);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /register root passkey/i }),
      ).toBeInTheDocument();
    });
  });

  it("shows already-done message when bootstrap not needed", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ needs_bootstrap: false }),
    });

    render(<BootstrapPage />);

    await waitFor(() => {
      expect(
        screen.getByText(/Root admin already has a passkey/),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /go to login/i }),
      ).toBeInTheDocument();
    });
  });

  it("resumes recovery without offering another passkey registration", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          needs_bootstrap: true,
          bootstrap_configured: true,
          stage: "recovery",
          policy_version: "2026-07-30",
          policy_sha256: "a".repeat(64),
          policy_text: "Permitted test data only.",
        }),
      })
      .mockResolvedValueOnce({ ok: false, status: 401 });

    render(<BootstrapPage />);

    expect(await screen.findByText(/only this recovery flow/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in with root passkey/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /register root passkey/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^create recovery key$/i })).not.toBeInTheDocument();
    expect(mockFetch.mock.calls[0][1]).toMatchObject({ cache: "no-store" });
  });

  it("shows error when bootstrap check fails", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
    });

    render(<BootstrapPage />);

    await waitFor(() => {
      expect(
        screen.getByText(/Failed to check bootstrap status/),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /retry/i }),
      ).toBeInTheDocument();
    });
  });

  it("shows error on network failure", async () => {
    mockFetch.mockRejectedValueOnce(new Error("Cannot reach server"));

    render(<BootstrapPage />);

    await waitFor(() => {
      expect(screen.getByText("Cannot reach server")).toBeInTheDocument();
    });
  });

  it("handles successful passkey registration", async () => {
    // bootstrap-status: needs bootstrap
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ needs_bootstrap: true }),
    });

    render(<BootstrapPage />);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /register root passkey/i }),
      ).toBeInTheDocument();
    });

    // begin returns options
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ options: JSON.stringify({ challenge: "abc" }), ceremony_id: 77 }),
    });
    mockStartRegistration.mockResolvedValueOnce({ id: "cred-1" });
    // complete succeeds
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({}),
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: /permitted-data boundary/i }));
    await user.type(screen.getByLabelText(/bootstrap code/i), "b".repeat(32));
    await user.click(
      screen.getByRole("button", { name: /register root passkey/i }),
    );

    await waitFor(() => {
      expect(
        screen.getByText(/only this recovery flow/i),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /sign in with root passkey/i }),
      ).toBeInTheDocument();
    });

    const completeBody = JSON.parse(mockFetch.mock.calls[2][1].body);
    expect(completeBody.credential.id).toBe("cred-1");
    expect(completeBody.ceremony_id).toBe(77);
    expect(mockFetch.mock.calls[1][1].headers["X-Bootstrap-Token"]).toBe(
      "b".repeat(32),
    );
  });

  it("unlocks normal access only after authenticated recovery download confirmation", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          needs_bootstrap: true,
          bootstrap_configured: true,
          stage: "recovery",
          policy_version: "2026-07-30",
          policy_sha256: "a".repeat(64),
          policy_text: "Permitted test data only.",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          is_root_admin: true,
          recovery_setup_required: true,
        }),
      });
    mockGenerateAgeRecoveryIdentity.mockResolvedValueOnce({
      recipient: `age1${"q".repeat(58)}`,
      identity: "AGE-SECRET-KEY-1SYNTHETICONLY",
    });

    render(<BootstrapPage />);

    const user = userEvent.setup();
    expect(await screen.findByRole("button", { name: /^create recovery key$/i })).toBeInTheDocument();
    expect(screen.queryByLabelText(/bootstrap code/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^create recovery key$/i }));
    await user.click(await screen.findByRole("button", { name: /download private recovery key/i }));
    await user.click(screen.getByRole("checkbox", { name: /was saved in a protected location/i }));
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ status: "ok" }) });
    await user.click(screen.getByRole("button", { name: /finish secure setup/i }));
    await user.click(await screen.findByRole("button", { name: /continue to administration/i }));
    expect(mockPush).toHaveBeenCalledWith("/admin");
    expect(mockFetch.mock.calls[2][0]).toContain("/bootstrap/recovery/complete");
    expect(JSON.parse(mockFetch.mock.calls[2][1].body)).toEqual({
      recipient: `age1${"q".repeat(58)}`,
      download_acknowledged: true,
    });
    expect(mockFetch.mock.calls[2][1].headers["X-Bootstrap-Token"]).toBeUndefined();
  });

  it("shows error when registration begin fails", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ needs_bootstrap: true }),
    });

    render(<BootstrapPage />);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /register root passkey/i }),
      ).toBeInTheDocument();
    });

    mockFetch.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ detail: "Server error" }),
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: /permitted-data boundary/i }));
    await user.type(screen.getByLabelText(/bootstrap code/i), "b".repeat(32));
    await user.click(
      screen.getByRole("button", { name: /register root passkey/i }),
    );

    await waitFor(() => {
      expect(screen.getByText("Server error")).toBeInTheDocument();
    });
  });

  it("returns to ready state when user cancels passkey prompt", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ needs_bootstrap: true }),
    });

    render(<BootstrapPage />);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /register root passkey/i }),
      ).toBeInTheDocument();
    });

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ options: JSON.stringify({ challenge: "abc" }) }),
    });

    const notAllowedError = new Error("User cancelled");
    notAllowedError.name = "NotAllowedError";
    mockStartRegistration.mockRejectedValueOnce(notAllowedError);

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: /permitted-data boundary/i }));
    await user.type(screen.getByLabelText(/bootstrap code/i), "b".repeat(32));
    await user.click(
      screen.getByRole("button", { name: /register root passkey/i }),
    );

    await waitFor(() => {
      // Should return to ready state, showing the register button again
      expect(
        screen.getByRole("button", { name: /register root passkey/i }),
      ).toBeInTheDocument();
    });
  });

  it("shows Welcome heading", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ needs_bootstrap: true }),
    });

    render(<BootstrapPage />);

    await waitFor(() => {
      expect(screen.getByText("Welcome")).toBeInTheDocument();
    });
  });

  it("retries on error", async () => {
    // First check fails
    mockFetch.mockRejectedValueOnce(new Error("Timeout"));

    render(<BootstrapPage />);

    await waitFor(() => {
      expect(screen.getByText("Timeout")).toBeInTheDocument();
    });

    // Second check succeeds
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ needs_bootstrap: true }),
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /register root passkey/i }),
      ).toBeInTheDocument();
    });
  });

  it("navigates to login from already-done state", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ needs_bootstrap: false }),
    });

    render(<BootstrapPage />);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /go to login/i }),
      ).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /go to login/i }));
    expect(mockPush).toHaveBeenCalledWith("/login");
  });
});
