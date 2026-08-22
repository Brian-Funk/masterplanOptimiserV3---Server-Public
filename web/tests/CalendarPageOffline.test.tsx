/**
 * Offline calendar rendering regressions.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

const mockPush = vi.hoisted(() => vi.fn());
const mockReplace = vi.hoisted(() => vi.fn());
const mockUseAuth = vi.hoisted(() => vi.fn());
const mockUseServiceAvailability = vi.hoisted(() => vi.fn());
const mockApiFetch = vi.hoisted(() => vi.fn());
const mockGetOfflineCalendarPayload = vi.hoisted(() => vi.fn());
const mockStoreOfflineCalendarPayload = vi.hoisted(() => vi.fn());
const mockOfflineCalendarStorageEnabled = vi.hoisted(() => vi.fn());
const mockSetOfflineCalendarStorageEnabled = vi.hoisted(() => vi.fn());
const mockClearOfflineCalendarCacheForUser = vi.hoisted(() => vi.fn());
const mockRoute = vi.hoisted(() => ({
  searchParams: new URLSearchParams("event=1"),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
  useSearchParams: () => mockRoute.searchParams,
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: mockUseAuth,
}));

vi.mock("@/contexts/ServiceAvailabilityContext", () => ({
  useServiceAvailability: () => mockUseServiceAvailability(),
}));

vi.mock("@/lib/api", () => ({
  apiFetch: mockApiFetch,
}));

vi.mock("@/lib/offlineCalendarCache", () => ({
  clearOfflineCalendarCacheForUser: mockClearOfflineCalendarCacheForUser,
  getOfflineCalendarPayload: mockGetOfflineCalendarPayload,
  OfflineCalendarStorageError: class OfflineCalendarStorageError extends Error {},
  offlineCalendarStorageEnabled: mockOfflineCalendarStorageEnabled,
  setOfflineCalendarStorageEnabled: mockSetOfflineCalendarStorageEnabled,
  storeOfflineCalendarPayload: mockStoreOfflineCalendarPayload,
}));

vi.mock("@/components/DynamicPWA", () => ({ DynamicPWA: () => null }));
vi.mock("@/components/ThemeToggle", () => ({ ThemeToggle: () => null }));
vi.mock("@/components/Logo", () => ({ Logo: () => <div>Logo</div> }));
vi.mock("@/components/Footer", () => ({ Footer: () => <footer /> }));
vi.mock("@/components/NotificationBell", () => ({ NotificationBell: () => null }));
vi.mock("@/components/AnnouncementBanner", () => ({ AnnouncementBanner: () => null }));
vi.mock("@/components/WebEditReviewModal", () => ({ WebEditReviewModal: () => null }));
vi.mock("@/components/ScheduleWebEditIndicator", () => ({
  ScheduleWebEditIndicator: () => null,
}));
vi.mock("@/components/TaskDetailModal", () => ({ TaskDetailModal: () => null }));
vi.mock("@/components/CreateTaskModal", () => ({ CreateTaskModal: () => null }));
vi.mock("@/components/ChangesModal", () => ({ ChangesModal: () => null }));
vi.mock("@/components/PublicScheduleCalendarGrid", () => ({
  PublicScheduleCalendarGrid: () => <div>Public schedule grid</div>,
}));
vi.mock("@/components/CalendarGrid", () => ({
  CalendarGrid: ({ tasks }: { tasks: Array<{ id: number; name: string }> }) => (
    <div data-testid="calendar-grid">
      {tasks.map((task) => (
        <span key={task.id}>{task.name}</span>
      ))}
    </div>
  ),
}));
vi.mock("@/components/DraftChangesPanel", () => ({
  DraftChangesPanel: ({ commitDisabled }: { commitDisabled: boolean }) => (
    <button disabled={commitDisabled}>Commit</button>
  ),
}));

const futureMarker = {
  schema_version: 2 as const,
  user_id: 42,
  event_id: 1,
  event_ref: "9d492121-5a09-4e8a-a0a9-3c873af85928",
  membership_id: 12,
  controller_public_id: "07096118-e64c-4b89-a7c9-1f26d6198719",
  controller_trust_entity_id: "ctl-0123456789abcdef",
  data_policy_version: 2,
  data_policy_sha256: "a".repeat(64),
  cached_at: "2026-05-21T09:30:00.000Z",
  valid_until: "2999-05-21T23:59:59.999Z",
  ttl_hours: 24,
};

const user = {
  id: 42,
  username: "viewer",
  display_name: "Viewer",
  email: null,
  is_root_admin: false,
  is_admin: false,
  is_issuer: false,
  can_edit: true,
  is_active: true,
  is_activated: true,
  linked_person_id: null,
  event_id: 1,
  event_ref: futureMarker.event_ref,
  membership_id: futureMarker.membership_id,
  controller_public_id: futureMarker.controller_public_id,
  controller_trust_entity_id: futureMarker.controller_trust_entity_id,
  data_policy_version: futureMarker.data_policy_version,
  data_policy_sha256: futureMarker.data_policy_sha256,
  offline_access_ttl_hours: 24,
};

const cachedCalendar = {
  offline_contract_version: "mp-opt-offline-calendar-v6",
  linked_person_id: null,
  controller_public_id: futureMarker.controller_public_id,
  controller_trust_entity_id: futureMarker.controller_trust_entity_id,
  event_ref: futureMarker.event_ref,
  membership_id: futureMarker.membership_id,
  event_id: 1,
  event_name: "Cached Masterplan",
  start_date: null,
  end_date: null,
  day_aliases: null,
  persons: [],
  public_schedule_items: [],
  tasks: [
    {
      id: 1,
      external_task_id: 10,
      name: "Opening Session",
      summary: null,
      description: null,
      start: "2026-05-21T09:00:00",
      end: "2026-05-21T10:00:00",
      location_name: "Hall A",
      location_address: null,
      task_type_code: null,
      task_type_name: null,
      color: null,
      attendees: [],
      field_assignments: null,
      field_values: null,
      field_definitions: null,
      additional: null,
      sort_order: 0,
      has_web_edit: false,
      web_edit_edited_at: null,
      web_edit_edited_by: null,
      web_edit_edited_by_user_id: null,
      web_edit_change_summary: [],
    },
  ],
};

function authState(overrides = {}) {
  return {
    user: null,
    logout: vi.fn(),
    isLoading: false,
    isAuthenticated: false,
    authStatus: "offline",
    offlineAccess: futureMarker,
    offlineAccessExpired: false,
    refreshUser: vi.fn(),
    ...overrides,
  };
}

describe("CalendarPage offline cache", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockReplace.mockReset();
    mockUseAuth.mockReset();
    mockUseServiceAvailability.mockReset();
    mockUseServiceAvailability.mockReturnValue({
      state: "device_offline",
      status: null,
      isReady: false,
      refresh: vi.fn(),
    });
    mockApiFetch.mockReset();
    mockGetOfflineCalendarPayload.mockReset();
    mockStoreOfflineCalendarPayload.mockReset();
    mockStoreOfflineCalendarPayload.mockResolvedValue({ schema_version: 2 });
    mockOfflineCalendarStorageEnabled.mockReset();
    mockOfflineCalendarStorageEnabled.mockReturnValue(true);
    mockSetOfflineCalendarStorageEnabled.mockReset();
    mockClearOfflineCalendarCacheForUser.mockReset();
    mockRoute.searchParams = new URLSearchParams("event=1");
    localStorage.clear();
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  it("renders a cached calendar while offline with a valid marker", async () => {
    mockUseAuth.mockReturnValue(authState());
    mockGetOfflineCalendarPayload.mockResolvedValue({
      user_id: 42,
      event_id: 1,
      cached_at: futureMarker.cached_at,
      payload: cachedCalendar,
    });

    const { default: CalendarPage } = await import("@/app/calendar/page");
    render(<CalendarPage />);

    expect(await screen.findByText("Cached Masterplan")).toBeInTheDocument();
    expect(screen.getByText("Opening Session")).toBeInTheDocument();
    expect(screen.getByText("You are offline")).toBeInTheDocument();
    expect(screen.getByText(/Showing the read-only schedule saved at/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Commit" })).toBeDisabled();
    expect(mockApiFetch).not.toHaveBeenCalled();
  });

  it("shows a calm empty state when no cached calendar exists", async () => {
    mockUseAuth.mockReturnValue(authState());
    mockGetOfflineCalendarPayload.mockResolvedValue(null);

    const { default: CalendarPage } = await import("@/app/calendar/page");
    render(<CalendarPage />);

    expect(await screen.findByText("You are offline")).toBeInTheDocument();
    expect(
      screen.getByText("Reconnect for live schedule access. No saved schedule is available on this device."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /view saved schedule/i })).not.toBeInTheDocument();
  });

  it("does not reveal cached data after offline access expires", async () => {
    mockUseAuth.mockReturnValue(
      authState({
        offlineAccess: null,
        offlineAccessExpired: true,
      }),
    );
    mockGetOfflineCalendarPayload.mockResolvedValue({
      user_id: 42,
      event_id: 1,
      cached_at: futureMarker.cached_at,
      payload: cachedCalendar,
    });

    const { default: CalendarPage } = await import("@/app/calendar/page");
    render(<CalendarPage />);

    expect(
      await screen.findByText("Saved-schedule access has expired. Reconnect and sign in again."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Cached Masterplan")).not.toBeInTheDocument();
    expect(mockGetOfflineCalendarPayload).not.toHaveBeenCalled();
  });

  it("falls back to cached calendar data after an online fetch failure", async () => {
    mockUseServiceAvailability.mockReturnValue({
      state: "ready",
      status: null,
      isReady: true,
      refresh: vi.fn(),
    });
    mockUseAuth.mockReturnValue(
      authState({
        user,
        isAuthenticated: true,
        authStatus: "authenticated",
      }),
    );
    mockApiFetch.mockRejectedValue(new TypeError("Failed to fetch"));
    mockGetOfflineCalendarPayload.mockResolvedValue({
      user_id: 42,
      event_id: 1,
      cached_at: futureMarker.cached_at,
      payload: cachedCalendar,
    });

    const { default: CalendarPage } = await import("@/app/calendar/page");
    render(<CalendarPage />);

    expect(await screen.findByText("Cached Masterplan")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith("/api/v1/calendar/1");
  });

  it("stores live calendar data after a successful online fetch", async () => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query === "(max-width: 767px)", media: query, onchange: null,
      addListener: vi.fn(), removeListener: vi.fn(),
      addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
    }));
    mockUseServiceAvailability.mockReturnValue({
      state: "ready",
      status: null,
      isReady: true,
      refresh: vi.fn(),
    });
    mockUseAuth.mockReturnValue(
      authState({
        user,
        isAuthenticated: true,
        authStatus: "authenticated",
      }),
    );
    mockApiFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => cachedCalendar,
    });

    const { default: CalendarPage } = await import("@/app/calendar/page");
    render(<CalendarPage />);

    await waitFor(() => {
      expect(mockStoreOfflineCalendarPayload).toHaveBeenCalledWith(
        42,
        1,
        cachedCalendar,
        expect.any(String),
        expect.any(String),
        undefined,
        expect.objectContaining({
          event_ref: futureMarker.event_ref,
          membership_id: futureMarker.membership_id,
          controller_public_id: futureMarker.controller_public_id,
        }),
      );
    });
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/v1/calendar/1/offline",
      { cache: "no-store" },
    );
  });

  it("dismisses the compact phone notice without deleting the offline copy", async () => {
    mockUseServiceAvailability.mockReturnValue({
      state: "ready", status: null, isReady: true, refresh: vi.fn(),
    });
    mockUseAuth.mockReturnValue(authState({
      user, isAuthenticated: true, authStatus: "authenticated",
    }));
    mockApiFetch.mockResolvedValue({
      ok: true, status: 200, json: async () => cachedCalendar,
    });

    const { default: CalendarPage } = await import("@/app/calendar/page");
    render(<CalendarPage />);

    expect(await screen.findByTestId("mobile-offline-schedule-notice")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Manage offline schedule" }));
    expect(mockPush).toHaveBeenCalledWith("/more?event=1#offline");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss offline schedule notice" }));
    expect(screen.queryByTestId("mobile-offline-schedule-notice")).not.toBeInTheDocument();
    expect(localStorage.getItem("mp-opt:offline-schedule-notice-dismissed:v1")).toBe("1");
    expect(mockClearOfflineCalendarCacheForUser).not.toHaveBeenCalled();
  });

  it("opens a linked organiser's own schedule first on a phone", async () => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query === "(max-width: 767px)",
      media: query,
      onchange: null,
      addListener: vi.fn(), removeListener: vi.fn(),
      addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
    }));
    const organiser = { ...user, is_admin: true, linked_person_id: 10 };
    const calendar = {
      ...cachedCalendar,
      persons: [
        { id: 1, external_person_id: 10, first_name: "Own", last_name: "Person" },
        { id: 2, external_person_id: 11, first_name: "Other", last_name: "Person" },
      ],
      tasks: [
        { ...cachedCalendar.tasks[0], id: 1, name: "My assignment", attendees: [{ name: "Own Person", person_id: 10 }] },
        { ...cachedCalendar.tasks[0], id: 2, name: "Someone else's assignment", attendees: [{ name: "Other Person", person_id: 11 }] },
      ],
      data_policy_acknowledged: true,
    };
    mockUseServiceAvailability.mockReturnValue({
      state: "ready", status: null, isReady: true, refresh: vi.fn(),
    });
    mockUseAuth.mockReturnValue(authState({
      user: organiser, isAuthenticated: true, authStatus: "authenticated",
    }));
    mockApiFetch.mockImplementation(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => url.includes("/web-edits")
        ? { total: 0, pending: 0, accepted: 0, rejected: 0 }
        : url.includes("/notifications/changes/")
          ? { changes: [] }
          : calendar,
    }));

    const { default: CalendarPage } = await import("@/app/calendar/page");
    render(<CalendarPage />);

    expect(await screen.findByText("My assignment")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Permitted-data policy" }).parentElement).toHaveClass(
      "hidden",
      "md:block",
    );
    await waitFor(() => expect(screen.queryByText("Someone else's assignment")).not.toBeInTheDocument());
  });

  it("does not download or refresh an offline copy on a wide screen", async () => {
    mockUseServiceAvailability.mockReturnValue({
      state: "ready", status: null, isReady: true, refresh: vi.fn(),
    });
    mockUseAuth.mockReturnValue(authState({
      user, isAuthenticated: true, authStatus: "authenticated",
    }));
    mockApiFetch.mockResolvedValue({
      ok: true, status: 200, json: async () => cachedCalendar,
    });

    const { default: CalendarPage } = await import("@/app/calendar/page");
    render(<CalendarPage />);

    expect(await screen.findByText("Cached Masterplan")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith("/api/v1/calendar/1");
    expect(mockApiFetch).not.toHaveBeenCalledWith(
      "/api/v1/calendar/1/offline",
      expect.anything(),
    );
    expect(mockStoreOfflineCalendarPayload).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /offline copy/i })).not.toBeInTheDocument();
  });

  it("keeps the complete organiser schedule as the default desktop view", async () => {
    const organiser = { ...user, is_admin: true, linked_person_id: 10 };
    const calendar = {
      ...cachedCalendar,
      persons: [
        { id: 1, external_person_id: 10, first_name: "Own", last_name: "Person" },
        { id: 2, external_person_id: 11, first_name: "Other", last_name: "Person" },
      ],
      tasks: [
        { ...cachedCalendar.tasks[0], id: 1, name: "My desktop assignment", attendees: [{ name: "Own Person", person_id: 10 }] },
        { ...cachedCalendar.tasks[0], id: 2, name: "Other desktop assignment", attendees: [{ name: "Other Person", person_id: 11 }] },
      ],
      data_policy_acknowledged: true,
    };
    mockUseServiceAvailability.mockReturnValue({
      state: "ready", status: null, isReady: true, refresh: vi.fn(),
    });
    mockUseAuth.mockReturnValue(authState({
      user: organiser, isAuthenticated: true, authStatus: "authenticated",
    }));
    mockApiFetch.mockImplementation(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => url.includes("/web-edits")
        ? { total: 0, pending: 0, accepted: 0, rejected: 0 }
        : url.includes("/notifications/changes/")
          ? { changes: [] }
          : calendar,
    }));

    const { default: CalendarPage } = await import("@/app/calendar/page");
    render(<CalendarPage />);

    expect(await screen.findByText("My desktop assignment")).toBeInTheDocument();
    expect(screen.getByText("Other desktop assignment")).toBeInTheDocument();
  });
});
