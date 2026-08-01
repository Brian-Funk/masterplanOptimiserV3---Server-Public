/**
 * Tests for the login page component.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

// Mock next/navigation
const mockPush = vi.fn();
const mockUseServiceAvailability = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

vi.mock("@/contexts/ServiceAvailabilityContext", () => ({
  useServiceAvailability: () => mockUseServiceAvailability(),
}));

// Mock @simplewebauthn/browser
vi.mock("@simplewebauthn/browser", () => ({
  startAuthentication: vi.fn(),
}));

// Mock AuthContext
const mockRefreshUser = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: vi.fn(() => ({
    user: null,
    isAuthenticated: false,
    isLoading: false,
    authStatus: "unauthenticated",
    offlineAccess: null,
    offlineAccessExpired: false,
    isLoggingOut: false,
    logoutError: null,
    logout: vi.fn(),
    dismissLogoutError: vi.fn(),
    refreshUser: mockRefreshUser,
  })),
}));

// Mock ThemeContext
vi.mock("@/contexts/ThemeContext", () => ({
  useTheme: () => ({ theme: "light", toggleTheme: vi.fn() }),
}));

// Mock fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// Import useAuth so we can reset it in beforeEach
import { useAuth } from "@/contexts/AuthContext";
import { startAuthentication } from "@simplewebauthn/browser";

function authBeginCalls() {
  return mockFetch.mock.calls.filter(([url]) =>
    String(url).includes("/api/v1/passkey/auth/begin"),
  );
}

beforeEach(() => {
  mockPush.mockReset();
  mockFetch.mockReset();
  mockRefreshUser.mockReset();
  vi.mocked(startAuthentication).mockReset();
  mockUseServiceAvailability.mockReset();
  mockUseServiceAvailability.mockReturnValue({
    state: "ready",
    status: null,
    isReady: true,
    refresh: vi.fn(),
  });
  // Reset useAuth to default unauthenticated state
  vi.mocked(useAuth).mockImplementation(() => ({
    user: null,
    isAuthenticated: false,
    isLoading: false,
    authStatus: "unauthenticated",
    offlineAccess: null,
    offlineAccessExpired: false,
    isLoggingOut: false,
    logoutError: null,
    logout: vi.fn(),
    dismissLogoutError: vi.fn(),
    refreshUser: mockRefreshUser,
  }));
});

describe("LoginPage", () => {
  it("renders login button", async () => {
    // Bootstrap check returns no bootstrap needed
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ needs_bootstrap: false }),
    });

    // Dynamic import to allow mocks to settle
    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    await waitFor(() => {
      const button = screen.getByRole("button", {
        name: /sign in with passkey/i,
      });
      expect(button).toBeTruthy();
    });
  });

  it("redirects to bootstrap when needed", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ needs_bootstrap: true }),
    });

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/bootstrap");
    });
  });

  it("redirects authenticated admin to /admin", async () => {
    const { useAuth } = await import("@/contexts/AuthContext");
    vi.mocked(useAuth).mockReturnValue({
      user: {
        id: 1,
        username: "admin",
        display_name: "Admin",
        email: null,
        is_root_admin: false,
        is_admin: true,
        is_issuer: false,
        can_edit: false,
        is_active: true,
        is_activated: true,
        linked_person_id: null,
        event_id: 1,
        offline_access_ttl_hours: 24,
      },
      isAuthenticated: true,
      isLoading: false,
      authStatus: "authenticated",
      offlineAccess: null,
      offlineAccessExpired: false,
      isLoggingOut: false,
      logoutError: null,
      logout: vi.fn(),
      dismissLogoutError: vi.fn(),
      refreshUser: vi.fn(),
    });

    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ needs_bootstrap: false }),
    });

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/admin");
    });
  });

  it("redirects issuer with event to /calendar", async () => {
    const { useAuth } = await import("@/contexts/AuthContext");
    vi.mocked(useAuth).mockReturnValue({
      user: {
        id: 2,
        username: "issuer1",
        display_name: "Issuer One",
        email: null,
        is_root_admin: false,
        is_admin: false,
        is_issuer: true,
        can_edit: false,
        is_active: true,
        is_activated: true,
        linked_person_id: 5,
        event_id: 3,
        offline_access_ttl_hours: 24,
      },
      isAuthenticated: true,
      isLoading: false,
      authStatus: "authenticated",
      offlineAccess: null,
      offlineAccessExpired: false,
      isLoggingOut: false,
      logoutError: null,
      logout: vi.fn(),
      dismissLogoutError: vi.fn(),
      refreshUser: vi.fn(),
    });

    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ needs_bootstrap: false }),
    });

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/calendar?event=3");
    });
  });

  it("redirects regular user with event to /calendar", async () => {
    const { useAuth } = await import("@/contexts/AuthContext");
    vi.mocked(useAuth).mockReturnValue({
      user: {
        id: 3,
        username: "viewer",
        display_name: "Viewer",
        email: null,
        is_root_admin: false,
        is_admin: false,
        is_issuer: false,
        can_edit: false,
        is_active: true,
        is_activated: true,
        linked_person_id: null,
        event_id: 2,
        offline_access_ttl_hours: 24,
      },
      isAuthenticated: true,
      isLoading: false,
      authStatus: "authenticated",
      offlineAccess: null,
      offlineAccessExpired: false,
      isLoggingOut: false,
      logoutError: null,
      logout: vi.fn(),
      dismissLogoutError: vi.fn(),
      refreshUser: vi.fn(),
    });

    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ needs_bootstrap: false }),
    });

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/calendar?event=2");
    });
  });

  it("offers the explicitly cached schedule while the service is offline", async () => {
    const { useAuth } = await import("@/contexts/AuthContext");
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      isAuthenticated: false,
      isLoading: false,
      authStatus: "offline",
      offlineAccess: {
        schema_version: 1,
        user_id: 3,
        event_id: 2,
        cached_at: "2026-05-21T09:30:00.000Z",
        valid_until: "2999-05-21T23:59:59.999Z",
        ttl_hours: 24,
      },
      offlineAccessExpired: false,
      isLoggingOut: false,
      logoutError: null,
      logout: vi.fn(),
      dismissLogoutError: vi.fn(),
      refreshUser: vi.fn(),
    });

    mockUseServiceAvailability.mockReturnValue({
      state: "device_offline",
      status: null,
      isReady: false,
      refresh: vi.fn(),
    });

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    const savedSchedule = await screen.findByRole("link", {
      name: /view saved schedule/i,
    });
    expect(savedSchedule).toHaveAttribute("href", "/calendar?event=2&mode=cached");
    expect(mockPush).not.toHaveBeenCalled();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("redirects user without event to /admin", async () => {
    const { useAuth } = await import("@/contexts/AuthContext");
    vi.mocked(useAuth).mockReturnValue({
      user: {
        id: 4,
        username: "noevent",
        display_name: "No Event",
        email: null,
        is_root_admin: false,
        is_admin: false,
        is_issuer: false,
        can_edit: false,
        is_active: true,
        is_activated: true,
        linked_person_id: null,
        event_id: null,
        offline_access_ttl_hours: 24,
      },
      isAuthenticated: true,
      isLoading: false,
      authStatus: "authenticated",
      offlineAccess: null,
      offlineAccessExpired: false,
      isLoggingOut: false,
      logoutError: null,
      logout: vi.fn(),
      dismissLogoutError: vi.fn(),
      refreshUser: vi.fn(),
    });

    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ needs_bootstrap: false }),
    });

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/admin");
    });
  });

  it("shows heading and subtitle", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ needs_bootstrap: false }),
    });

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    await waitFor(() => {
      expect(screen.getByText("Masterplan Optimiser")).toBeInTheDocument();
      expect(screen.getByText("Sign in to your account")).toBeInTheDocument();
    });
  });

  it("button is not disabled by default", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ needs_bootstrap: false }),
    });

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    await waitFor(() => {
      const button = screen.getByRole("button", {
        name: /sign in with passkey/i,
      });
      expect(button).not.toBeDisabled();
    });
  });


  it("passes ceremony_id from auth begin to auth complete", async () => {
    const { startAuthentication } = await import("@simplewebauthn/browser");

    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({}),
    });
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ needs_bootstrap: false }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          options: JSON.stringify({ challenge: "auth-challenge" }),
          ceremony_id: 42,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ exchange_code: "exchange-1" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      });
    vi.mocked(startAuthentication).mockResolvedValueOnce({
      id: "cred-1",
      rawId: "cred-1",
      response: {},
      type: "public-key",
    } as never);

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: /sign in with passkey/i }),
    );

    await waitFor(() => {
      expect(
        mockFetch.mock.calls.some(([url]) =>
          String(url).includes("/api/v1/passkey/auth/complete"),
        ),
      ).toBe(true);
    });

    const completeCall = mockFetch.mock.calls.find(([url]) =>
      String(url).includes("/api/v1/passkey/auth/complete"),
    );
    const completeBody = JSON.parse(completeCall?.[1].body);
    expect(completeBody.credential.id).toBe("cred-1");
    expect(completeBody.ceremony_id).toBe(42);

    const beginCall = mockFetch.mock.calls.find(([url]) =>
      String(url).includes("/api/v1/passkey/auth/begin"),
    );
    expect(beginCall?.[1].body).toBeUndefined();
  });

  it("shows discoverable-passkey guidance when the credential manager cannot find a passkey", async () => {
    const { startAuthentication } = await import("@simplewebauthn/browser");
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(startAuthentication).mockRejectedValueOnce(
      Object.assign(
        new Error("An unknown error occurred while talking to the credential manager"),
        { name: "UnknownError" },
      ),
    );

    mockFetch.mockImplementation((url) => {
      if (String(url).includes("/api/v1/passkey/bootstrap-status")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ needs_bootstrap: false }),
        });
      }
      if (String(url).includes("/api/v1/passkey/auth/begin")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            options: JSON.stringify({ challenge: "auth-challenge" }),
            ceremony_id: 7,
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({}),
      });
    });

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    try {
      const user = userEvent.setup();
      await user.click(
        await screen.findByRole("button", { name: /sign in with passkey/i }),
      );

      expect(
        await screen.findByText(/No usable passkey was found on this device/i),
      ).toBeInTheDocument();
      expect(screen.queryByLabelText(/account name/i)).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: /use account name/i }),
      ).not.toBeInTheDocument();
    } finally {
      consoleSpy.mockRestore();
    }
  });

  it("ignores duplicate passkey clicks while the first ceremony is starting", async () => {
    const { startAuthentication } = await import("@simplewebauthn/browser");
    vi.mocked(startAuthentication).mockRejectedValueOnce(
      Object.assign(new Error("User cancelled"), { name: "NotAllowedError" }),
    );

    let resolveBegin!: (value: {
      ok: boolean;
      json: () => Promise<{ options: string; ceremony_id: number }>;
    }) => void;

    mockFetch.mockImplementation((url) => {
      if (String(url).includes("/api/v1/passkey/bootstrap-status")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ needs_bootstrap: false }),
        });
      }
      if (String(url).includes("/api/v1/passkey/auth/begin")) {
        return new Promise((resolve) => {
          resolveBegin = resolve;
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({}),
      });
    });

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    const button = await screen.findByRole("button", {
      name: /sign in with passkey/i,
    });
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() => {
      expect(authBeginCalls()).toHaveLength(1);
    });

    resolveBegin({
      ok: true,
      json: async () => ({
        options: JSON.stringify({ challenge: "auth-challenge" }),
        ceremony_id: 7,
      }),
    });

    await waitFor(() => {
      expect(button).not.toBeDisabled();
    });
  });

  it("clears the in-flight guard after passkey cancellation so retry starts a fresh ceremony", async () => {
    const { startAuthentication } = await import("@simplewebauthn/browser");
    vi.mocked(startAuthentication)
      .mockRejectedValueOnce(
        Object.assign(
          new Error("The operation either timed out or was not allowed."),
          { name: "AbortError" },
        ),
      )
      .mockRejectedValueOnce(
        Object.assign(new Error("User cancelled"), { name: "NotAllowedError" }),
      );

    let ceremonyId = 0;
    mockFetch.mockImplementation((url) => {
      if (String(url).includes("/api/v1/passkey/bootstrap-status")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ needs_bootstrap: false }),
        });
      }
      if (String(url).includes("/api/v1/passkey/auth/begin")) {
        ceremonyId += 1;
        return Promise.resolve({
          ok: true,
          json: async () => ({
            options: JSON.stringify({ challenge: `auth-${ceremonyId}` }),
            ceremony_id: ceremonyId,
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({}),
      });
    });

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    const user = userEvent.setup();
    const button = await screen.findByRole("button", {
      name: /sign in with passkey/i,
    });

    await user.click(button);
    await waitFor(() => {
      expect(authBeginCalls()).toHaveLength(1);
      expect(
        screen.queryByText(/operation either timed out or was not allowed/i),
      ).not.toBeInTheDocument();
    });

    await user.click(button);
    await waitFor(() => {
      expect(authBeginCalls()).toHaveLength(2);
    });
  });

  it("still shows real backend passkey verification failures", async () => {
    const { startAuthentication } = await import("@simplewebauthn/browser");

    mockFetch.mockImplementation((url) => {
      if (String(url).includes("/api/v1/passkey/bootstrap-status")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ needs_bootstrap: false }),
        });
      }
      if (String(url).includes("/api/v1/passkey/auth/begin")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            options: JSON.stringify({ challenge: "auth-challenge" }),
            ceremony_id: 99,
          }),
        });
      }
      if (String(url).includes("/api/v1/passkey/auth/complete")) {
        return Promise.resolve({
          ok: false,
          json: async () => ({ detail: "Passkey verification failed" }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({}),
      });
    });
    vi.mocked(startAuthentication).mockResolvedValueOnce({
      id: "cred-1",
      rawId: "cred-1",
      response: {},
      type: "public-key",
    } as never);

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: /sign in with passkey/i }),
    );

    expect(
      await screen.findByText("Passkey verification failed"),
    ).toBeInTheDocument();
  });

  it("stays on login when bootstrap check fails", async () => {
    mockFetch.mockRejectedValue(new Error("Network error"));

    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);

    // Should not redirect - stays on login.
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /sign in with passkey/i }),
      ).toBeInTheDocument();
    });
    expect(mockPush).not.toHaveBeenCalled();
  });
});
