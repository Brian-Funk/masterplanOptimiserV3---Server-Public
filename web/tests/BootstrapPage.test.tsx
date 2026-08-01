/**
 * Tests for BootstrapPage - initial passkey registration flow.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

// Mock next/navigation
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
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

// Mock fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

import BootstrapPage from "@/app/bootstrap/page";

beforeEach(() => {
  mockPush.mockReset();
  mockFetch.mockReset();
  mockStartRegistration.mockReset();
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
        screen.getByText(/Root passkey registered successfully/),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /sign in and create recovery key/i }),
      ).toBeInTheDocument();
    });

    const completeBody = JSON.parse(mockFetch.mock.calls[2][1].body);
    expect(completeBody.credential.id).toBe("cred-1");
    expect(completeBody.ceremony_id).toBe(77);
    expect(mockFetch.mock.calls[1][1].headers["X-Bootstrap-Token"]).toBe(
      "b".repeat(32),
    );
  });

  it("navigates to login after successful registration", async () => {
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
    mockStartRegistration.mockResolvedValueOnce({ id: "cred-1" });
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
        screen.getByRole("button", { name: /sign in and create recovery key/i }),
      ).toBeInTheDocument();
    });

    await user.click(
      screen.getByRole("button", { name: /sign in and create recovery key/i }),
    );
    expect(mockPush).toHaveBeenCalledWith("/login?next=/recovery-key");
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
