import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ClientProviders from "@/app/ClientProviders";
import { useServiceAvailability } from "@/contexts/ServiceAvailabilityContext";

const { mockFetch, mockPathname } = vi.hoisted(() => ({
  mockFetch: vi.fn(),
  mockPathname: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname(),
}));
vi.mock("@/contexts/ThemeContext", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/InstallPrompt", () => ({ InstallPrompt: () => null }));
vi.mock("@/lib/environment", () => ({ getApiUrl: () => "https://server.test" }));
vi.mock("@/lib/offlineCalendarCache", () => ({
  clearLegacyPrivateCaches: vi.fn().mockResolvedValue(undefined),
  clearOfflineCalendarCacheForUser: vi.fn().mockResolvedValue(undefined),
}));

function AvailabilityProbe() {
  const { isReady } = useServiceAvailability();
  return <div>{isReady ? "Service ready" : "Checking service"}</div>;
}

describe("ClientProviders", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockPathname.mockReset();
    vi.stubGlobal("fetch", mockFetch);
  });

  const readyStatus = {
    format: "mp-opt-ha-public-status-v1",
    mode: "standalone",
    state: "ready",
    reason: null,
    observed_at: "2026-07-19T10:00:00Z",
    transition_started_at: null,
    earliest_failover_at: null,
    recovery_point_at: null,
    retry_after_seconds: 0,
    capabilities: {
      sign_in: true,
      live_reads: true,
      writes: true,
      public_links: true,
    },
    last_recovery: null,
  };

  it("checks public availability but not an authenticated session on the shared schedule route", async () => {
    mockPathname.mockReturnValue("/shared-schedule");
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => readyStatus,
    });

    render(
      <ClientProviders>
        <div>Shared schedule</div>
        <AvailabilityProbe />
      </ClientProviders>,
    );

    expect(screen.getByText("Shared schedule")).toBeInTheDocument();
    expect(await screen.findByText("Service ready")).toBeInTheDocument();
    expect(mockFetch).toHaveBeenCalledWith(
      "/ha/status",
      expect.objectContaining({ cache: "no-store", signal: expect.anything() }),
    );
    expect(
      mockFetch.mock.calls.some(([url]) => String(url).includes("/api/v1/auth/me")),
    ).toBe(false);
  });

  it("retains the authenticated session check on private application routes", async () => {
    mockPathname.mockReturnValue("/calendar");
    mockFetch.mockImplementation((url) => {
      if (String(url) === "/ha/status") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => readyStatus,
        });
      }
      return Promise.resolve({ ok: false, status: 401 });
    });

    render(
      <ClientProviders>
        <div>Calendar</div>
      </ClientProviders>,
    );

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "https://server.test/api/v1/auth/me",
        { credentials: "include" },
      );
    });
  });
});
