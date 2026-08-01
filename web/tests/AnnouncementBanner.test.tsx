/**
 * Tests for AnnouncementBanner component.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

// Mock environment
vi.mock("@/lib/environment", () => ({
  getApiUrl: () => "https://api.test",
}));

// Mock fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// CSRF cookie
Object.defineProperty(document, "cookie", {
  writable: true,
  value: "csrf_token=test-csrf",
});

// Mock lucide-react
vi.mock("lucide-react", () => ({
  Megaphone: (props: Record<string, unknown>) =>
    React.createElement("svg", { "data-testid": "megaphone-icon", ...props }),
  X: (props: Record<string, unknown>) =>
    React.createElement("svg", { "data-testid": "x-icon", ...props }),
}));

// Mock sessionStorage
const sessionStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value;
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key];
    }),
    clear: () => {
      store = {};
    },
  };
})();
vi.stubGlobal("sessionStorage", sessionStorageMock);

import { AnnouncementBanner } from "@/components/AnnouncementBanner";

const mockAnnouncements = [
  {
    id: 1,
    title: "System maintenance",
    body: "Downtime scheduled for tonight.",
    created_by: "admin",
    created_at: "2026-01-01T12:00:00Z",
  },
  {
    id: 2,
    title: "New feature available",
    body: null,
    created_by: "admin",
    created_at: "2026-01-02T12:00:00Z",
  },
];

beforeEach(() => {
  mockFetch.mockReset();
  sessionStorageMock.clear();
  sessionStorageMock.getItem.mockClear();
  sessionStorageMock.setItem.mockClear();
});

describe("AnnouncementBanner", () => {
  it("renders announcements from API", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => mockAnnouncements,
    });

    render(<AnnouncementBanner eventId={1} />);

    await waitFor(() => {
      expect(screen.getByText("System maintenance")).toBeInTheDocument();
      expect(screen.getByText("New feature available")).toBeInTheDocument();
    });
  });

  it("renders announcement body when present", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => mockAnnouncements,
    });

    render(<AnnouncementBanner eventId={1} />);

    await waitFor(() => {
      expect(
        screen.getByText("Downtime scheduled for tonight."),
      ).toBeInTheDocument();
    });
  });

  it("renders nothing when no announcements", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });

    const { container } = render(<AnnouncementBanner eventId={1} />);

    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
  });

  it("renders nothing when API fails", async () => {
    mockFetch.mockRejectedValueOnce(new Error("Network error"));

    const { container } = render(<AnnouncementBanner eventId={1} />);

    // Wait a tick for the effect to settle
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
  });

  it("dismisses an announcement on click", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [mockAnnouncements[0]],
    });

    const user = userEvent.setup();
    render(<AnnouncementBanner eventId={1} />);

    await waitFor(() => {
      expect(screen.getByText("System maintenance")).toBeInTheDocument();
    });

    await user.click(
      screen.getByRole("button", { name: /dismiss announcement/i }),
    );

    expect(screen.queryByText("System maintenance")).toBeNull();
  });

  it("saves dismissed IDs to sessionStorage", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [mockAnnouncements[0]],
    });

    const user = userEvent.setup();
    render(<AnnouncementBanner eventId={5} />);

    await waitFor(() => {
      expect(screen.getByText("System maintenance")).toBeInTheDocument();
    });

    await user.click(
      screen.getByRole("button", { name: /dismiss announcement/i }),
    );

    expect(sessionStorageMock.setItem).toHaveBeenCalledWith(
      "mp-dismissed-5",
      JSON.stringify([1]),
    );
  });

  it("loads previously dismissed IDs from sessionStorage", async () => {
    sessionStorageMock.setItem("mp-dismissed-1", JSON.stringify([1]));
    sessionStorageMock.setItem.mockClear();

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => mockAnnouncements,
    });

    render(<AnnouncementBanner eventId={1} />);

    await waitFor(() => {
      // Announcement 1 should be hidden (dismissed)
      expect(screen.queryByText("System maintenance")).toBeNull();
      // Announcement 2 should still be visible
      expect(screen.getByText("New feature available")).toBeInTheDocument();
    });
  });

  it("does not fetch when eventId is 0", () => {
    render(<AnnouncementBanner eventId={0} />);
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
