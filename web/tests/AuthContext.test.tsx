/**
 * Tests for AuthContext - authentication state management.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import React from "react";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import { storeOfflineAccessForCalendar } from "@/lib/offlineAccess";

const { mockHardNavigate } = vi.hoisted(() => ({
  mockHardNavigate: vi.fn(),
}));
vi.mock("@/lib/hardNavigation", () => ({
  hardNavigate: mockHardNavigate,
}));

vi.mock("@/contexts/ServiceAvailabilityContext", () => ({
  useServiceAvailability: () => ({
    state: "ready",
    status: null,
    isReady: true,
    refresh: vi.fn(),
  }),
}));

// Mock fetch globally
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// Helper component that displays auth state
function AuthConsumer() {
  const { user, isAuthenticated, isLoading } = useAuth();
  if (isLoading) return <div>Loading...</div>;
  if (!isAuthenticated) return <div>Not authenticated</div>;
  return (
    <div>
      <span data-testid="username">{user!.username}</span>
      <span data-testid="role">
        {user!.is_admin ? "admin" : user!.is_issuer ? "issuer" : "user"}
      </span>
    </div>
  );
}

function renderWithAuth() {
  return render(
    <AuthProvider>
      <AuthConsumer />
    </AuthProvider>,
  );
}

function deferredResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

const mockUser = {
  id: 1,
  username: "testadmin",
  display_name: "Test Admin",
  email: "admin@test.com",
  is_root_admin: false,
  is_admin: true,
  is_issuer: false,
  can_edit: true,
  is_active: true,
  is_activated: true,
  linked_person_id: null,
  event_id: 1,
  offline_access_ttl_hours: 24,
};

beforeEach(() => {
  mockFetch.mockReset();
  mockHardNavigate.mockReset();
  localStorage.clear();
  window.history.replaceState({}, "", "/");
});

describe("AuthContext", () => {
  it("shows loading state initially", () => {
    mockFetch.mockImplementation(() => new Promise(() => {})); // never resolves
    renderWithAuth();
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("sets user when /auth/me returns 200", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => mockUser,
    });

    renderWithAuth();

    await waitFor(() => {
      expect(screen.getByTestId("username")).toHaveTextContent("testadmin");
    });
    expect(screen.getByTestId("role")).toHaveTextContent("admin");
  });

  it("sets user to null when /auth/me returns 401", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 401 });

    renderWithAuth();

    await waitFor(() => {
      expect(screen.getByText("Not authenticated")).toBeInTheDocument();
    });
  });

  it("fences a recovery-pending root away from normal application pages", async () => {
    window.history.replaceState({}, "", "/admin");
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...mockUser,
        is_root_admin: true,
        commissioning_required: true,
        commissioning_stage: "recovery",
      }),
    });

    renderWithAuth();

    await waitFor(() => {
      expect(mockHardNavigate).toHaveBeenCalledWith("/setup");
    });
    expect(localStorage.getItem("mp_opt_offline_access")).toBeNull();
  });

  it("ignores an older session response after a newer refresh succeeds", async () => {
    const older = deferredResponse();
    mockFetch
      .mockReturnValueOnce(older.promise)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => mockUser,
      });

    function RefreshConsumer() {
      const { user, refreshUser } = useAuth();
      return (
        <div>
          <span data-testid="refreshed-user">{user?.username ?? "none"}</span>
          <button type="button" onClick={() => void refreshUser()}>Refresh</button>
        </div>
      );
    }

    render(
      <AuthProvider>
        <RefreshConsumer />
      </AuthProvider>,
    );

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    await act(async () => {
      screen.getByRole("button", { name: "Refresh" }).click();
    });
    await waitFor(() => {
      expect(screen.getByTestId("refreshed-user")).toHaveTextContent("testadmin");
    });

    await act(async () => {
      older.resolve({ ok: false, status: 401 } as Response);
      await older.promise;
    });
    expect(screen.getByTestId("refreshed-user")).toHaveTextContent("testadmin");
  });

  it("handles fetch error gracefully", async () => {
    mockFetch.mockRejectedValueOnce(new Error("Network error"));

    renderWithAuth();

    await waitFor(() => {
      expect(screen.getByText("Not authenticated")).toBeInTheDocument();
    });
  });

  it("keeps offline auth state when session check fails with a valid marker", async () => {
    storeOfflineAccessForCalendar(mockUser, 1);
    mockFetch.mockRejectedValueOnce(new Error("Network error"));

    function OfflineConsumer() {
      const { authStatus, offlineAccess, isLoading } = useAuth();
      if (isLoading) return <div>Loading...</div>;
      return (
        <div>
          <span data-testid="status">{authStatus}</span>
          <span data-testid="event">{offlineAccess?.event_id ?? "none"}</span>
        </div>
      );
    }

    render(
      <AuthProvider>
        <OfflineConsumer />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("offline");
      expect(screen.getByTestId("event")).toHaveTextContent("1");
    });
  });

  it("treats an upstream 503 as offline uncertainty instead of logout", async () => {
    storeOfflineAccessForCalendar(mockUser, 1);
    mockFetch.mockResolvedValueOnce({ ok: false, status: 503 });

    function OfflineConsumer() {
      const { authStatus, offlineAccess, isLoading } = useAuth();
      if (isLoading) return <div>Loading...</div>;
      return <div>{`${authStatus}:${offlineAccess?.event_id ?? "none"}`}</div>;
    }

    render(
      <AuthProvider>
        <OfflineConsumer />
      </AuthProvider>,
    );

    expect(await screen.findByText("offline:1")).toBeInTheDocument();
  });

  it("denies expired offline access and removes its marker", async () => {
    localStorage.setItem("mp-opt-offline-access", JSON.stringify({
      schema_version: 1,
      user_id: 1,
      event_id: 1,
      cached_at: "2000-01-01T00:00:00.000Z",
      valid_until: "2000-01-01T01:00:00.000Z",
      ttl_hours: 1,
    }));
    mockFetch.mockRejectedValueOnce(new Error("Network error"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const error = vi.spyOn(console, "error").mockImplementation(() => {});

    function ExpiredConsumer() {
      const { authStatus, offlineAccess, offlineAccessExpired, isLoading } = useAuth();
      if (isLoading) return <div>Loading...</div>;
      return <div>{`${authStatus}:${offlineAccess ? "stored" : "empty"}:${offlineAccessExpired}`}</div>;
    }

    render(
      <AuthProvider>
        <ExpiredConsumer />
      </AuthProvider>,
    );

    expect(await screen.findByText("offline:empty:true")).toBeInTheDocument();
    expect(localStorage.getItem("mp-opt-offline-access")).toBeNull();
    warn.mockRestore();
    error.mockRestore();
  });

  it("stores the configured offline cached view window in the local marker", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-21T09:30:00Z"));

    try {
      const marker = storeOfflineAccessForCalendar(
        { ...mockUser, offline_access_ttl_hours: 3 },
        1,
      );

      expect(marker.ttl_hours).toBe(3);
      expect(marker.valid_until).toBe("2026-05-21T12:30:00.000Z");
      expect(
        JSON.parse(localStorage.getItem("mp-opt-offline-access") ?? "{}"),
      ).toMatchObject({
        schema_version: 1,
        ttl_hours: 3,
        valid_until: "2026-05-21T12:30:00.000Z",
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("identifies issuer role correctly", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...mockUser,
        is_admin: false,
        is_issuer: true,
      }),
    });

    renderWithAuth();

    await waitFor(() => {
      expect(screen.getByTestId("role")).toHaveTextContent("issuer");
    });
  });

  it("identifies root admin correctly", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...mockUser,
        is_root_admin: true,
        is_admin: true,
      }),
    });

    renderWithAuth();

    await waitFor(() => {
      expect(screen.getByTestId("username")).toHaveTextContent("testadmin");
      expect(screen.getByTestId("role")).toHaveTextContent("admin");
    });
  });

  it("identifies regular user correctly", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...mockUser,
        is_admin: false,
        is_issuer: false,
      }),
    });

    renderWithAuth();

    await waitFor(() => {
      expect(screen.getByTestId("role")).toHaveTextContent("user");
    });
  });
});

describe("AuthContext logout", () => {
  it("sets user to null after logout", async () => {
    // First call: /auth/me returns a user
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => mockUser,
    });
    // Second call: /auth/logout
    mockFetch.mockResolvedValueOnce({ ok: true });

    function LogoutConsumer() {
      const { isAuthenticated, isLoading, logout } = useAuth();
      if (isLoading) return <div>Loading...</div>;
      return (
        <div>
          <span data-testid="auth">{isAuthenticated ? "yes" : "no"}</span>
          <button onClick={logout}>Logout</button>
        </div>
      );
    }

    render(
      <AuthProvider>
        <LogoutConsumer />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("auth")).toHaveTextContent("yes");
    });

    await act(async () => {
      screen.getByText("Logout").click();
    });

    await waitFor(() => {
      expect(screen.getByTestId("auth")).toHaveTextContent("no");
    });
  });

  it("calls /auth/logout endpoint with POST", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => mockUser,
    });
    mockFetch.mockResolvedValueOnce({ ok: true });

    function LogoutConsumer() {
      const { isLoading, logout } = useAuth();
      if (isLoading) return <div>Loading...</div>;
      return <button onClick={logout}>Logout</button>;
    }

    render(
      <AuthProvider>
        <LogoutConsumer />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("Logout")).toBeInTheDocument();
    });

    await act(async () => {
      screen.getByText("Logout").click();
    });

    // Second fetch call should be the logout POST
    const logoutCall = mockFetch.mock.calls[1];
    expect(logoutCall[0]).toContain("/api/v1/auth/logout");
    expect(logoutCall[1].method).toBe("POST");
    expect(logoutCall[1].credentials).toBe("include");
    expect(logoutCall[1].body).toBe(JSON.stringify({}));
    expect(logoutCall[1].headers["Content-Type"]).toBe("application/json");
  });

  it("handles logout error gracefully", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => mockUser,
    });
    mockFetch.mockRejectedValueOnce(new Error("Network error"));

    function LogoutConsumer() {
      const { isAuthenticated, isLoading, logout } = useAuth();
      if (isLoading) return <div>Loading...</div>;
      return (
        <div>
          <span data-testid="auth">{isAuthenticated ? "yes" : "no"}</span>
          <button onClick={logout}>Logout</button>
        </div>
      );
    }

    const spy = vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <AuthProvider>
        <LogoutConsumer />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("auth")).toHaveTextContent("yes");
    });

    await act(async () => {
      screen.getByText("Logout").click();
    });

    // On network error, local state should not pretend server logout succeeded.
    await waitFor(() => {
      expect(screen.getByTestId("auth")).toHaveTextContent("yes");
    });

    spy.mockRestore();
  });

  it("keeps the user authenticated when logout returns a non-ok response", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => mockUser,
    });
    mockFetch.mockResolvedValueOnce({ ok: false, status: 415 });

    function LogoutConsumer() {
      const { isAuthenticated, isLoading, logout } = useAuth();
      if (isLoading) return <div>Loading...</div>;
      return (
        <div>
          <span data-testid="auth">{isAuthenticated ? "yes" : "no"}</span>
          <button onClick={logout}>Logout</button>
        </div>
      );
    }

    const spy = vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <AuthProvider>
        <LogoutConsumer />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("auth")).toHaveTextContent("yes");
    });

    await act(async () => {
      screen.getByText("Logout").click();
    });

    await waitFor(() => {
      expect(screen.getByTestId("auth")).toHaveTextContent("yes");
    });

    spy.mockRestore();
  });
  it("refreshUser re-fetches and updates context", async () => {
    // Initial fetch: user is admin
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => mockUser,
    });

    function RefreshConsumer() {
      const { user, isLoading, refreshUser } = useAuth();
      if (isLoading) return <div>Loading...</div>;
      return (
        <div>
          <span data-testid="name">{user?.display_name || "none"}</span>
          <button onClick={refreshUser}>Refresh</button>
        </div>
      );
    }

    render(
      <AuthProvider>
        <RefreshConsumer />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("name")).toHaveTextContent("Test Admin");
    });

    // Set up response for refresh call
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...mockUser,
        display_name: "Updated Admin",
      }),
    });

    await act(async () => {
      screen.getByText("Refresh").click();
    });

    await waitFor(() => {
      expect(screen.getByTestId("name")).toHaveTextContent("Updated Admin");
    });
  });
});

describe("useAuth outside provider", () => {
  it("throws when used outside AuthProvider", () => {
    // Suppress React error boundary noise
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});

    function Bare() {
      useAuth();
      return null;
    }

    expect(() => render(<Bare />)).toThrow(
      "useAuth must be used within an AuthProvider",
    );

    spy.mockRestore();
  });
});
