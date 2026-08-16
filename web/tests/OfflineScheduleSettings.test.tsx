import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OfflineScheduleSettings } from "@/components/OfflineScheduleSettings";

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(), enabled: vi.fn(), setEnabled: vi.fn(), store: vi.fn(),
  clear: vi.fn(), clearMarker: vi.fn(), commitMarker: vi.fn(),
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: 9, event_id: 3, offline_access_ttl_hours: 24 } }),
}));
vi.mock("@/lib/api", () => ({ apiFetch: mocks.apiFetch }));
vi.mock("@/lib/offlineCalendarCache", () => ({
  offlineCalendarStorageEnabled: mocks.enabled,
  setOfflineCalendarStorageEnabled: mocks.setEnabled,
  storeOfflineCalendarPayload: mocks.store,
  clearOfflineCalendarCacheForUser: mocks.clear,
}));
vi.mock("@/lib/offlineAccess", () => ({
  buildOfflineAccessForCalendar: () => ({ valid_until: "2999-01-01T00:00:00Z" }),
  clearOfflineAccessMarker: mocks.clearMarker,
  commitOfflineAccessMarker: mocks.commitMarker,
}));

describe("OfflineScheduleSettings", () => {
  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    mocks.enabled.mockReturnValue(false);
    mocks.store.mockResolvedValue({ schema_version: 3 });
    mocks.clear.mockResolvedValue(undefined);
    mocks.apiFetch.mockResolvedValue({ ok: true, json: async () => ({ event_id: 3 }) });
  });

  it("claims availability only after download and IndexedDB storage succeed", async () => {
    render(<OfflineScheduleSettings eventId={3} />);
    fireEvent.click(screen.getByRole("button", { name: "Enable offline copy" }));

    await waitFor(() => expect(mocks.store).toHaveBeenCalled());
    expect(mocks.apiFetch).toHaveBeenCalledWith("/api/v1/calendar/3/offline", { cache: "no-store" });
    expect(mocks.commitMarker).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Remove offline copy" })).toBeInTheDocument();
  });

  it("fails closed when payload validation or storage fails", async () => {
    mocks.store.mockRejectedValue(new Error("The offline payload is unsafe."));
    render(<OfflineScheduleSettings eventId={3} />);
    fireEvent.click(screen.getByRole("button", { name: "Enable offline copy" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The offline payload is unsafe.");
    expect(mocks.commitMarker).not.toHaveBeenCalled();
    expect(mocks.setEnabled).toHaveBeenLastCalledWith(9, false);
  });

  it("removes the stored copy and marker", async () => {
    mocks.enabled.mockReturnValue(true);
    render(<OfflineScheduleSettings eventId={3} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove offline copy" }));

    await waitFor(() => expect(mocks.clear).toHaveBeenCalledWith(9));
    expect(mocks.clearMarker).toHaveBeenCalledOnce();
    expect(mocks.setEnabled).toHaveBeenCalledWith(9, false);
  });
});
