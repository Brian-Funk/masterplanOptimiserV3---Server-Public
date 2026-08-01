import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.hoisted(() => vi.fn());

vi.mock("@/contexts/ServiceAvailabilityContext", () => ({
  useServiceAvailability: () => ({
    state: "ready",
    status: null,
    isReady: true,
    refresh: vi.fn(),
  }),
}));

vi.mock("@/components/Logo", () => ({ Logo: () => <div>Masterplan Optimiser</div> }));
vi.mock("@/components/ThemeToggle", () => ({ ThemeToggle: () => <button>Theme</button> }));
vi.mock("@/lib/environment", () => ({ getApiUrl: () => "https://server.test" }));

const payload = {
  event: {
    name: "Annual Session",
    start_date: "2026-08-01",
    end_date: "2026-08-02",
    day_aliases: { "2026-08-01": "Day 1" },
  },
  views: [
    { id: 10, name: "Delegates", sort_order: 0 },
    { id: 11, name: "Officials", sort_order: 1 },
  ],
  items: [
    {
      id: 1,
      view_id: 10,
      title: "Opening Briefing",
      date: "2026-08-01",
      start_time: "09:00",
      end_time: "10:00",
      location_name: "Room A",
      location_address: "1 Parliament Square",
      responsible: "Session president",
      audience_teams: [{ name: "Delegates", short_name: "DEL", colour: "#336699" }],
      description: "Bring laptops.",
      type_name: "Briefing",
      colour: "#336699",
      sort_order: 0,
    },
    {
      id: 2,
      view_id: 11,
      title: "Board Update",
      date: "2026-08-01",
      start_time: "11:00",
      end_time: "12:00",
      location_name: null,
      location_address: null,
      responsible: null,
      audience_teams: [],
      description: null,
      type_name: null,
      colour: null,
      sort_order: 0,
    },
  ],
};

describe("SharedSchedulePage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    vi.stubGlobal("fetch", mockFetch);
    window.history.replaceState({}, "", "/shared-schedule");
  });

  it("loads the fragment token through a bearer header and renders only public controls", async () => {
    mockFetch.mockResolvedValue({ ok: true, json: async () => payload });
    window.history.replaceState({}, "", "/shared-schedule#token=shared-token-value-12345");
    const user = userEvent.setup();
    const { default: SharedSchedulePage } = await import("@/app/shared-schedule/page");

    render(<SharedSchedulePage />);

    expect(await screen.findByText("Annual Session")).toBeInTheDocument();
    expect(screen.getAllByText("Opening Briefing").length).toBeGreaterThan(0);
    expect(screen.queryByText("Board Update")).not.toBeInTheDocument();
    expect(screen.queryByText(/highlight/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/assigned people/i)).not.toBeInTheDocument();
    expect(window.location.hash).toBe("");
    expect(mockFetch).toHaveBeenCalledWith(
      "https://server.test/api/v1/public-schedule/shared",
      expect.objectContaining({
        headers: { Authorization: "Bearer shared-token-value-12345" },
        cache: "no-store",
      }),
    );

    await user.dblClick(
      screen.getByRole("button", { name: "View details for Opening Briefing" }),
    );
    expect(screen.getByText("Room A - 1 Parliament Square")).toBeInTheDocument();
    expect(screen.getByText("Session president")).toBeInTheDocument();
    expect(screen.getByText("Bring laptops.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close schedule details" }));

    await user.click(screen.getByRole("tab", { name: "Officials" }));
    expect(screen.getAllByText("Board Update").length).toBeGreaterThan(0);
    expect(screen.queryByText("Opening Briefing")).not.toBeInTheDocument();
  });

  it("shows one generic unavailable state when the token is rejected", async () => {
    mockFetch.mockResolvedValue({ ok: false, json: async () => ({}) });
    window.history.replaceState({}, "", "/shared-schedule#token=rejected-token-value-12345");
    const { default: SharedSchedulePage } = await import("@/app/shared-schedule/page");

    render(<SharedSchedulePage />);

    await waitFor(() => {
      expect(screen.getByText("Shared schedule unavailable")).toBeInTheDocument();
    });
    expect(screen.getByText("This link is invalid, expired or no longer available.")).toBeInTheDocument();
  });
});
